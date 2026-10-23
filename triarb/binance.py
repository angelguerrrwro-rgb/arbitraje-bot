"""Cliente REST mínimo de Binance Spot (firmado con HMAC-SHA256)."""

from __future__ import annotations

import hashlib
import hmac
import json
import statistics
import time
from urllib.parse import urlencode

import aiohttp

from .models import SymbolInfo

try:
    import orjson

    loads = orjson.loads
except ImportError:  # pragma: no cover
    loads = json.loads

LIVE_REST = "https://api.binance.com"
LIVE_WS = "wss://stream.binance.com:9443/stream"
TESTNET_REST = "https://testnet.binance.vision"
TESTNET_WS = "wss://stream.testnet.binance.vision/stream"


class BinanceError(Exception):
    def __init__(self, status: int, code, msg: str):
        super().__init__(f"HTTP {status} (código {code}): {msg}")
        self.status = status
        self.code = code
        self.msg = msg


class BinanceRest:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str = "", api_secret: str = ""):
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.time_offset_ms = 0.0
        self.used_weight = 0

    async def request(self, method: str, path: str, params: dict | None = None, signed: bool = False):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {}
        if signed:
            if not self.api_key or not self.api_secret:
                raise BinanceError(0, None, "faltan las claves de API en .env")
            headers["X-MBX-APIKEY"] = self.api_key
            params["timestamp"] = int(time.time() * 1000 + self.time_offset_ms)
            params.setdefault("recvWindow", 5000)
            query = urlencode(params)
            query += "&signature=" + hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        else:
            query = urlencode(params)
        url = self.base_url + path + ("?" + query if query else "")
        async with self.session.request(method, url, headers=headers) as resp:
            text = await resp.text()
            weight = resp.headers.get("x-mbx-used-weight-1m")
            if weight:
                self.used_weight = int(weight)
            try:
                data = loads(text) if text else None
            except ValueError:
                data = None
            if resp.status >= 400:
                code = data.get("code") if isinstance(data, dict) else None
                msg = data.get("msg") if isinstance(data, dict) else text[:200]
                raise BinanceError(resp.status, code, msg)
            return data

    async def ping_ms(self, samples: int = 6) -> float:
        """Mediana del tiempo de ida y vuelta (la primera muestra incluye el TLS y se descarta)."""
        times = []
        for _ in range(samples):
            t = time.perf_counter()
            await self.request("GET", "/api/v3/ping")
            times.append((time.perf_counter() - t) * 1000)
        return statistics.median(times[1:] if len(times) > 1 else times)

    async def sync_time(self, samples: int = 5) -> None:
        """Desfase reloj local → Binance, con la muestra de menor ida y vuelta (la más precisa)."""
        best = None
        for _ in range(samples):
            t0 = time.time() * 1000
            data = await self.request("GET", "/api/v3/time")
            t1 = time.time() * 1000
            if best is None or t1 - t0 < best[0]:
                best = (t1 - t0, data["serverTime"] - (t0 + t1) / 2)
        self.time_offset_ms = best[1]

    async def exchange_info(self) -> dict:
        return await self.request("GET", "/api/v3/exchangeInfo")

    async def ticker_prices(self) -> dict[str, float]:
        data = await self.request("GET", "/api/v3/ticker/price")
        return {d["symbol"]: float(d["price"]) for d in data}

    async def book_tickers(self) -> list[dict]:
        """Mejor bid/ask de todos los pares en una sola petición (peso 4)."""
        return await self.request("GET", "/api/v3/ticker/bookTicker")

    async def balances(self) -> dict[str, float]:
        data = await self.request("GET", "/api/v3/account", {"omitZeroBalances": "true"}, signed=True)
        return {b["asset"]: float(b["free"]) for b in data["balances"]}

    async def new_order(self, **params) -> dict:
        params["newOrderRespType"] = "FULL"
        return await self.request("POST", "/api/v3/order", params, signed=True)


def _clean(num: str) -> str:
    return num.rstrip("0").rstrip(".") if "." in num else num


def parse_symbols(info: dict) -> dict[str, SymbolInfo]:
    """Pares en estado TRADING con sus filtros LOT_SIZE, PRICE_FILTER y NOTIONAL."""
    out: dict[str, SymbolInfo] = {}
    for s in info.get("symbols", []):
        if s.get("status") != "TRADING" or not s.get("isSpotTradingAllowed", True):
            continue
        order_types = s.get("orderTypes", [])
        if "LIMIT" not in order_types or "MARKET" not in order_types:
            continue
        f = {x["filterType"]: x for x in s.get("filters", [])}
        lot = f.get("LOT_SIZE", {})
        mlot = f.get("MARKET_LOT_SIZE", {})
        price = f.get("PRICE_FILTER", {})
        notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
        step_str = _clean(lot.get("stepSize", "0"))
        mstep_str = _clean(mlot.get("stepSize", "0"))
        if float(mstep_str or 0) == 0:
            mstep_str = step_str
        out[s["symbol"]] = SymbolInfo(
            symbol=s["symbol"],
            base=s["baseAsset"],
            quote=s["quoteAsset"],
            step=float(step_str),
            step_str=step_str,
            min_qty=float(lot.get("minQty", 0)),
            market_step=float(mstep_str),
            market_step_str=mstep_str,
            market_min_qty=float(mlot.get("minQty", 0) or 0),
            tick_str=_clean(price.get("tickSize", "0")),
            min_notional=float(notional.get("minNotional", 0) or 0),
            quote_precision=int(s.get("quoteAssetPrecision", s.get("quotePrecision", 8))),
            quote_market_allowed=bool(s.get("quoteOrderQtyMarketAllowed", True)),
        )
    return out
