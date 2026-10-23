"""Carry en una sola plataforma: spot comprado + perpetuo vendido del mismo activo.

La posición es neutral al precio (lo que gana una pata lo pierde la otra) y cobra el funding cada
vez que es positivo (los largos pagan a los cortos). Con oro tokenizado (PAXG, XAUT) el funding
fue positivo en ~90 % de los pagos de los últimos 90 días (+5–9 %/año bruto).

El bot:
- puntúa cada candidato con el funding medio de los últimos `ventana_dias` (pasado, no futuro);
- entra en el mejor si supera `minimo_anual`; solo cambia si otro le saca `margen_cambio`
  (cambiar cuesta 4 comisiones); sale si el funding medio cae por debajo de `salida_anual`;
- cobra cada pago de funding con la tasa oficial publicada por el exchange;
- vigila el margen del corto (a qué subida de precio se liquidaría);
- guarda el estado en JSON para seguir tras un reinicio y apunta todo en logs/carry.csv.

Modo papel: datos públicos y ejecución simulada a mejor bid/ask con comisiones. Sin claves.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

log = logging.getLogger("triarb.carry")

HOUR_MS = 3_600_000


@dataclass
class Candidate:
    asset: str
    spot: str
    perp: str


CANDIDATES = {
    "binance": [Candidate("PAXG", "PAXGUSDT", "PAXGUSDT"), Candidate("XAUT", "XAUTUSDT", "XAUTUSDT"),
                Candidate("BTC", "BTCUSDT", "BTCUSDT"), Candidate("ETH", "ETHUSDT", "ETHUSDT")],
    "bybit": [Candidate("XAUT", "XAUTUSDT", "XAUTUSDT"), Candidate("BTC", "BTCUSDT", "BTCUSDT"),
              Candidate("ETH", "ETHUSDT", "ETHUSDT")],
}
FEES = {"binance": (0.00075, 0.0005), "bybit": (0.001, 0.00055)}  # (spot taker, perp taker)


# ---------------------------------------------------------------- datos públicos de cada plataforma

class Binance:
    name = "binance"

    def __init__(self, session: aiohttp.ClientSession):
        self.s = session

    async def _get(self, url, **params):
        async with self.s.get(url, params=params) as r:
            r.raise_for_status()
            return await r.json(content_type=None)

    async def quotes(self, c: Candidate) -> dict:
        sp, pb, pi = await asyncio.gather(
            self._get("https://api.binance.com/api/v3/ticker/bookTicker", symbol=c.spot),
            self._get("https://fapi.binance.com/fapi/v1/ticker/bookTicker", symbol=c.perp),
            self._get("https://fapi.binance.com/fapi/v1/premiumIndex", symbol=c.perp),
        )
        return {"spot_bid": float(sp["bidPrice"]), "spot_ask": float(sp["askPrice"]),
                "perp_bid": float(pb["bidPrice"]), "perp_ask": float(pb["askPrice"]),
                "mark": float(pi["markPrice"]), "next_funding": int(pi["nextFundingTime"]),
                "predicted": float(pi["lastFundingRate"])}

    async def funding(self, c: Candidate, since_ms: int) -> list[tuple[int, float]]:
        out, start = [], since_ms
        while True:
            rows = await self._get("https://fapi.binance.com/fapi/v1/fundingRate", symbol=c.perp,
                                   startTime=start, limit=1000)
            out += [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows]
            if len(rows) < 1000:
                return out
            start = out[-1][0] + 1


class Bybit:
    name = "bybit"

    def __init__(self, session: aiohttp.ClientSession):
        self.s = session

    async def _get(self, url, **params):
        async with self.s.get(url, params=params) as r:
            r.raise_for_status()
            return (await r.json(content_type=None))["result"]

    async def quotes(self, c: Candidate) -> dict:
        sp, pp = await asyncio.gather(
            self._get("https://api.bybit.com/v5/market/tickers", category="spot", symbol=c.spot),
            self._get("https://api.bybit.com/v5/market/tickers", category="linear", symbol=c.perp),
        )
        s, p = sp["list"][0], pp["list"][0]
        return {"spot_bid": float(s["bid1Price"]), "spot_ask": float(s["ask1Price"]),
                "perp_bid": float(p["bid1Price"]), "perp_ask": float(p["ask1Price"]),
                "mark": float(p["markPrice"]), "next_funding": int(p["nextFundingTime"]),
                "predicted": float(p["fundingRate"])}

    async def funding(self, c: Candidate, since_ms: int) -> list[tuple[int, float]]:
        out, end = [], int(time.time() * 1000)
        while end > since_ms:
            res = await self._get("https://api.bybit.com/v5/market/funding/history", category="linear",
                                  symbol=c.perp, startTime=since_ms, endTime=end, limit=200)
            rows = res["list"]
            if not rows:
                break
            out += [(int(r["fundingRateTimestamp"]), float(r["fundingRate"])) for r in rows]
            end = min(int(r["fundingRateTimestamp"]) for r in rows) - 1
            if len(rows) < 200:
                break
        return sorted(out)


VENUES = {"binance": Binance, "bybit": Bybit}


# ---------------------------------------------------------------- decisión (funciones puras)

def annual_rate(rows: list[tuple[int, float]], since_ms: int, now_ms: int) -> float | None:
    """Funding medio anualizado en [since, now], en fracción. None si no hay datos suficientes."""
    sel = [r for t, r in rows if since_ms <= t <= now_ms]
    span_days = (now_ms - since_ms) / 86_400_000
    if len(sel) < 3 or span_days <= 0:
        return None
    return sum(sel) / span_days * 365


def decide(scores: dict[str, float | None], current: str | None, minimum: float, exit_below: float,
           switch_margin: float) -> str | None:
    """Activo en el que hay que estar (o None). Evita cambiar por poco: cada cambio cuesta comisiones."""
    valid = {a: s for a, s in scores.items() if s is not None}
    best = max(valid, key=valid.get) if valid else None
    if current is not None:
        cur = valid.get(current)
        if cur is None or cur < exit_below:
            return best if best is not None and valid[best] >= minimum else None
        if best is not None and best != current and valid[best] - cur >= switch_margin:
            return best
        return current
    if best is not None and valid[best] >= minimum:
        return best
    return None


# ---------------------------------------------------------------- cartera simulada

@dataclass
class Position:
    asset: str
    spot: str
    perp: str
    qty: float
    spot_entry: float
    perp_entry: float
    margin: float
    opened_ms: int
    last_funding_ms: int


@dataclass
class Book:
    """Contabilidad del modo papel. `cash` incluye el margen reservado del corto."""

    cash: float
    position: Position | None = None
    funding_received: float = 0.0
    fees_paid: float = 0.0
    trading_pnl: float = 0.0
    started_ms: int = 0
    initial: float = 0.0

    def open(self, c: Candidate, q: dict, leverage: float, utilization: float, fees: tuple[float, float],
             now_ms: int) -> Position:
        spot_fee, perp_fee = fees
        notional = self.cash * utilization / (1 + 1 / leverage)
        qty = notional / q["spot_ask"]
        cost = qty * q["spot_ask"] * (1 + spot_fee)
        fee = qty * q["spot_ask"] * spot_fee + qty * q["perp_bid"] * perp_fee
        self.cash -= cost + qty * q["perp_bid"] * perp_fee
        self.fees_paid += fee
        self.position = Position(c.asset, c.spot, c.perp, qty, q["spot_ask"], q["perp_bid"],
                                 qty * q["perp_bid"] / leverage, now_ms, now_ms)
        return self.position

    def close(self, q: dict, fees: tuple[float, float]) -> float:
        p = self.position
        spot_fee, perp_fee = fees
        proceeds = p.qty * q["spot_bid"] * (1 - spot_fee)
        perp_pnl = p.qty * (p.perp_entry - q["perp_ask"]) - p.qty * q["perp_ask"] * perp_fee
        self.cash += proceeds + perp_pnl
        self.fees_paid += p.qty * q["spot_bid"] * spot_fee + p.qty * q["perp_ask"] * perp_fee
        pnl = (q["spot_bid"] - p.spot_entry) * p.qty + (p.perp_entry - q["perp_ask"]) * p.qty
        self.trading_pnl += pnl
        self.position = None
        return pnl

    def credit_funding(self, rate: float, mark: float) -> float:
        """El corto cobra qty × mark × tasa (si la tasa es negativa, paga)."""
        amount = self.position.qty * mark * rate
        self.cash += amount
        self.funding_received += amount
        return amount

    def equity(self, q: dict | None) -> float:
        if self.position is None or q is None:
            return self.cash
        p = self.position
        return self.cash + p.qty * q["spot_bid"] + p.qty * (p.perp_entry - q["mark"])

    def liquidation_move(self, q: dict, maintenance: float = 0.005) -> float:
        """Subida de precio (fracción) que liquidaría el corto si no se repone margen."""
        p = self.position
        # margen + (entrada − precio)·qty = mantenimiento·precio·qty  →  precio de liquidación
        liq = (p.margin + p.perp_entry * p.qty) / (p.qty * (1 + maintenance))
        return liq / q["mark"] - 1

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Book":
        pos = d.pop("position")
        book = cls(**d)
        book.position = Position(**pos) if pos else None
        return book


# ---------------------------------------------------------------- bucle 24/7

@dataclass
class CarryConfig:
    venue: str = "binance"
    capital: float = 500.0
    leverage: float = 2.0
    utilization: float = 0.95
    window_days: float = 14.0
    minimum: float = 0.04  # 4 %/año para entrar
    exit_below: float = 0.0  # salir si el funding medio se vuelve negativo
    switch_margin: float = 0.10  # cambiar solo si otro paga ≥10 %/año más (cambiar cuesta 4 comisiones)
    poll_s: float = 60.0
    decide_every_s: float = 3600.0
    min_liq_move: float = 0.25  # avisar si una subida del 25 % liquidaría el corto
    folder: str = "logs"
    assets: list[str] = field(default_factory=list)


class Carry:
    def __init__(self, cfg: CarryConfig, session: aiohttp.ClientSession):
        self.cfg = cfg
        self.venue = VENUES[cfg.venue](session)
        self.fees = FEES[cfg.venue]
        cands = CANDIDATES[cfg.venue]
        if cfg.assets:
            cands = [c for c in cands if c.asset in cfg.assets]
        self.cands = {c.asset: c for c in cands}
        self.dir = Path(cfg.folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / f"carry_estado_{cfg.venue}.json"
        self.csv_path = self.dir / "carry.csv"
        now = int(time.time() * 1000)
        if self.state_path.exists():
            self.book = Book.from_json(json.loads(self.state_path.read_text(encoding="utf-8")))
            log.info("Estado recuperado: %.2f $ de capital simulado", self.book.equity(None))
        else:
            self.book = Book(cash=cfg.capital, started_ms=now, initial=cfg.capital)
        self.history: dict[str, list[tuple[int, float]]] = {}
        self.last_decision = 0.0

    def _event(self, kind: str, **data) -> None:
        new = not self.csv_path.exists()
        with open(self.csv_path, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["fecha", "plataforma", "evento", "activo", "detalle", "capital_usd"])
            w.writerow([datetime.now().isoformat(timespec="seconds"), self.cfg.venue, kind, data.pop("asset", ""),
                        json.dumps(data, ensure_ascii=False), f"{data.get('equity', self.book.cash):.4f}"])
        log.info("%s %s", kind, data)

    def _save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.book.to_json(), indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    async def refresh_history(self) -> None:
        since = int((time.time() - (self.cfg.window_days + 1) * 86400) * 1000)
        for a, c in self.cands.items():
            try:
                self.history[a] = await self.venue.funding(c, since)
            except Exception as e:
                log.warning("No se pudo leer el funding de %s: %s", a, e)

    def scores(self, now_ms: int) -> dict[str, float | None]:
        since = now_ms - int(self.cfg.window_days * 86_400_000)
        return {a: annual_rate(self.history.get(a, []), since, now_ms) for a in self.cands}

    async def step(self) -> str:
        now = int(time.time() * 1000)
        book, cfg = self.book, self.cfg
        pos = book.position
        q = await self.venue.quotes(self.cands[pos.asset]) if pos else None

        # 1. Cobrar los pagos de funding publicados desde el último registrado
        if pos:
            new = [(t, r) for t, r in await self.venue.funding(self.cands[pos.asset], pos.last_funding_ms + 1)
                   if t > pos.last_funding_ms]
            for t, r in new:
                amount = book.credit_funding(r, q["mark"])
                pos.last_funding_ms = t
                self._event("funding", asset=pos.asset, tasa=r, importe=round(amount, 6), equity=book.equity(q))

        # 2. Decidir (cada hora): entrar, cambiar o salir
        if time.time() - self.last_decision >= cfg.decide_every_s:
            self.last_decision = time.time()
            await self.refresh_history()
            sc = self.scores(now)
            target = decide(sc, pos.asset if pos else None, cfg.minimum, cfg.exit_below, cfg.switch_margin)
            shown = {a: (round(s * 100, 2) if s is not None else None) for a, s in sc.items()}
            if pos and target != pos.asset:
                pnl = book.close(q, self.fees)
                self._event("cierre", asset=pos.asset, motivo="cambio" if target else "funding bajo",
                            pnl_precio=round(pnl, 6), funding_anual=shown, equity=book.cash)
                pos, q = None, None
            if target and not pos:
                c = self.cands[target]
                q = await self.venue.quotes(c)
                pos = book.open(c, q, cfg.leverage, cfg.utilization, self.fees, now)
                self._event("apertura", asset=target, cantidad=pos.qty, spot=pos.spot_entry, perp=pos.perp_entry,
                            funding_anual=shown, equity=book.equity(q))

        # 3. Vigilar el margen del corto
        if pos and q:
            move = book.liquidation_move(q)
            if move < cfg.min_liq_move:
                self._event("aviso_margen", asset=pos.asset, subida_que_liquida=round(move, 4), equity=book.equity(q))
        self._save()
        eq = book.equity(q)
        days = (now - book.started_ms) / 86_400_000
        # Anualizar con menos de un día de datos da cifras absurdas (las comisiones de entrada dominan)
        yearly = f"{(eq / book.initial - 1) / days * 365 * 100:+.1f} %/año" if days >= 1 else "anualizado desde el día 1"
        return (f"{datetime.now():%H:%M} {cfg.venue} · {'en ' + pos.asset if pos else 'sin posición'} · "
                f"capital {eq:.2f} $ ({(eq / book.initial - 1) * 100:+.3f} %) · funding cobrado "
                f"{book.funding_received:+.4f} $ · comisiones {book.fees_paid:.4f} $ · {yearly}"
                + (f" · se liquidaría con +{book.liquidation_move(q) * 100:.0f} %" if pos and q else ""))

    async def run(self, duration_s: float | None = None) -> None:
        end = time.time() + duration_s if duration_s else None
        while end is None or time.time() < end:
            try:
                print(await self.step(), flush=True)
            except Exception as e:  # red caída, API caída: reintentar en la próxima vuelta
                log.warning("Error en la vuelta del carry: %s", e)
            await asyncio.sleep(self.cfg.poll_s)


# ---------------------------------------------------------------- backtest con funding histórico

def backtest(history: dict[str, list[tuple[int, float]]], cfg: CarryConfig) -> dict:
    """Recorre los pagos reales de funding con las mismas reglas de decisión (cada pago es un paso).
    No modela los cambios de precio entre patas (pequeños en una posición neutral)."""
    spot_fee, perp_fee = FEES[cfg.venue]
    switch_cost = 2 * (spot_fee + perp_fee)  # cerrar y abrir, las dos patas
    times = sorted({t for rows in history.values() for t, _ in rows})
    by = {a: dict(rows) for a, rows in history.items()}
    per_notional = 1 / (1 + 1 / cfg.leverage) * cfg.utilization
    current, ret, switches, held = None, 0.0, 0, {}
    start = times[0] + int(cfg.window_days * 86_400_000)
    for t in times:
        if t < start:
            continue
        sc = {a: annual_rate(rows, t - int(cfg.window_days * 86_400_000), t - 1) for a, rows in history.items()}
        target = decide(sc, current, cfg.minimum, cfg.exit_below, cfg.switch_margin)
        if target != current:
            ret -= (switch_cost if current and target else switch_cost / 2) * per_notional
            switches += 1
            current = target
        if current:
            ret += by[current].get(t, 0.0) * per_notional
            held[current] = held.get(current, 0) + 1
    if current:
        ret -= switch_cost / 2 * per_notional
    days = (times[-1] - start) / 86_400_000
    return {"return": ret, "annual": ret / days * 365 if days > 0 else 0.0, "switches": switches,
            "held": held, "days": days}
