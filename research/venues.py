"""Adaptadores de datos públicos de varios exchanges: pares (base, quote) y mejor bid/ask de todos.

Cada adaptador devuelve:
- markets(): {símbolo del exchange: (BASE, QUOTE)} normalizados en mayúsculas
- tickers(): {símbolo del exchange: (bid, bid_qty, ask, ask_qty)}  (qty = nan si el exchange no la da)
"""

from __future__ import annotations

import math

import aiohttp

NAN = math.nan
ALIASES = {"XBT": "BTC", "XDG": "DOGE"}


def norm(asset: str) -> str:
    a = asset.upper()
    return ALIASES.get(a, a)


def f(x) -> float:
    try:
        v = float(x)
        return v if v > 0 else NAN
    except (TypeError, ValueError):
        return NAN


async def _get(s: aiohttp.ClientSession, url: str, **params):
    async with s.get(url, params=params or None) as r:
        r.raise_for_status()
        return await r.json(content_type=None)


class Binance:
    name = "binance"

    async def markets(self, s):
        d = await _get(s, "https://api.binance.com/api/v3/exchangeInfo")
        return {x["symbol"]: (norm(x["baseAsset"]), norm(x["quoteAsset"])) for x in d["symbols"] if x["status"] == "TRADING"}

    async def tickers(self, s):
        d = await _get(s, "https://api.binance.com/api/v3/ticker/bookTicker")
        return {x["symbol"]: (f(x["bidPrice"]), f(x["bidQty"]), f(x["askPrice"]), f(x["askQty"])) for x in d}


class OKX:
    name = "okx"

    async def markets(self, s):
        d = await _get(s, "https://www.okx.com/api/v5/public/instruments", instType="SPOT")
        return {x["instId"]: (norm(x["baseCcy"]), norm(x["quoteCcy"])) for x in d["data"] if x["state"] == "live"}

    async def tickers(self, s):
        d = await _get(s, "https://www.okx.com/api/v5/market/tickers", instType="SPOT")
        return {x["instId"]: (f(x["bidPx"]), f(x["bidSz"]), f(x["askPx"]), f(x["askSz"])) for x in d["data"]}


class Bybit:
    name = "bybit"

    async def markets(self, s):
        d = await _get(s, "https://api.bybit.com/v5/market/instruments-info", category="spot")
        return {x["symbol"]: (norm(x["baseCoin"]), norm(x["quoteCoin"])) for x in d["result"]["list"]
                if x["status"] == "Trading"}

    async def tickers(self, s):
        d = await _get(s, "https://api.bybit.com/v5/market/tickers", category="spot")
        return {x["symbol"]: (f(x["bid1Price"]), f(x["bid1Size"]), f(x["ask1Price"]), f(x["ask1Size"]))
                for x in d["result"]["list"]}


class KuCoin:
    name = "kucoin"

    async def markets(self, s):
        d = await _get(s, "https://api.kucoin.com/api/v2/symbols")
        return {x["symbol"]: (norm(x["baseCurrency"]), norm(x["quoteCurrency"])) for x in d["data"] if x["enableTrading"]}

    async def tickers(self, s):
        d = await _get(s, "https://api.kucoin.com/api/v1/market/allTickers")
        return {x["symbol"]: (f(x.get("buy")), f(x.get("bestBidSize")), f(x.get("sell")), f(x.get("bestAskSize")))
                for x in d["data"]["ticker"]}


class Gate:
    name = "gate"

    async def markets(self, s):
        d = await _get(s, "https://api.gateio.ws/api/v4/spot/currency_pairs")
        return {x["id"]: (norm(x["base"]), norm(x["quote"])) for x in d if x.get("trade_status") == "tradable"}

    async def tickers(self, s):
        d = await _get(s, "https://api.gateio.ws/api/v4/spot/tickers")
        return {x["currency_pair"]: (f(x.get("highest_bid")), f(x.get("highest_size")), f(x.get("lowest_ask")),
                                     f(x.get("lowest_size"))) for x in d}


class MEXC:
    name = "mexc"

    async def markets(self, s):
        d = await _get(s, "https://api.mexc.com/api/v3/exchangeInfo")
        return {x["symbol"]: (norm(x["baseAsset"]), norm(x["quoteAsset"])) for x in d["symbols"]
                if x.get("isSpotTradingAllowed", True) and str(x.get("status")) in ("1", "ENABLED", "TRADING")}

    async def tickers(self, s):
        d = await _get(s, "https://api.mexc.com/api/v3/ticker/bookTicker")
        return {x["symbol"]: (f(x["bidPrice"]), f(x["bidQty"]), f(x["askPrice"]), f(x["askQty"])) for x in d}


class Bitget:
    name = "bitget"

    async def markets(self, s):
        d = await _get(s, "https://api.bitget.com/api/v2/spot/public/symbols")
        return {x["symbol"]: (norm(x["baseCoin"]), norm(x["quoteCoin"])) for x in d["data"] if x["status"] == "online"}

    async def tickers(self, s):
        d = await _get(s, "https://api.bitget.com/api/v2/spot/market/tickers")
        return {x["symbol"]: (f(x.get("bidPr")), f(x.get("bidSz")), f(x.get("askPr")), f(x.get("askSz")))
                for x in d["data"]}


class HTX:
    name = "htx"

    async def markets(self, s):
        d = await _get(s, "https://api.huobi.pro/v2/settings/common/symbols")
        return {x["sc"]: (norm(x["bc"]), norm(x["qc"])) for x in d["data"] if x.get("state") == "online"}

    async def tickers(self, s):
        d = await _get(s, "https://api.huobi.pro/market/tickers")
        return {x["symbol"]: (f(x.get("bid")), f(x.get("bidSize")), f(x.get("ask")), f(x.get("askSize")))
                for x in d["data"]}


class Kraken:
    name = "kraken"

    async def markets(self, s):
        d = await _get(s, "https://api.kraken.com/0/public/AssetPairs")
        out = {}
        for key, x in d["result"].items():
            ws = x.get("wsname", "")
            if "/" in ws and x.get("status", "online") == "online":
                b, q = ws.split("/")
                out[key] = (norm(b), norm(q))
        return out

    async def tickers(self, s):
        d = await _get(s, "https://api.kraken.com/0/public/Ticker")
        return {k: (f(x["b"][0]), f(x["b"][2]), f(x["a"][0]), f(x["a"][2])) for k, x in d["result"].items()}


ALL = [Binance(), OKX(), Bybit(), KuCoin(), Gate(), MEXC(), Bitget(), HTX(), Kraken()]
