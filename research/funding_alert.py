"""Alerta de funding: avisa cuando el "cash and carry" empieza a compensar.

Cada hora mira el funding de los pares elegidos. Si su equivalente anual supera el umbral
durante N comprobaciones seguidas, avisa por Telegram (TELEGRAM_TOKEN / TELEGRAM_CHAT_ID en .env)
y por consola. Con el mercado de octubre de 2026 (BTC ≈ 0 %) no saltará; está pensado para
dejarlo corriendo en el servidor y enterarte cuando el mercado se caliente.

Uso: python -m research.funding_alert --pares BTCUSDT ETHUSDT --umbral 15 --seguidas 6
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time

import aiohttp

from triarb.config import load_env
from triarb.notify import Telegram

FAPI = "https://fapi.binance.com/fapi/v1/premiumIndex"


async def main(pairs: list[str], threshold: float, needed: int, every_min: float) -> None:
    load_env()
    streak = {p: 0 for p in pairs}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        tg = Telegram(s, os.environ.get("TELEGRAM_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", ""), True)
        while True:
            try:
                async with s.get(FAPI) as r:
                    data = {d["symbol"]: d for d in await r.json()}
            except Exception as e:
                print(time.strftime("%H:%M"), "error consultando funding:", e, flush=True)
                await asyncio.sleep(60)
                continue
            line = []
            for p in pairs:
                d = data.get(p)
                if not d:
                    continue
                apr = float(d["lastFundingRate"]) * 3 * 365 * 100  # aproximación con pagos cada 8 h
                streak[p] = streak[p] + 1 if apr >= threshold else 0
                line.append(f"{p} {apr:+.1f} %/año ({streak[p]})")
                if streak[p] == needed:
                    msg = (f"Funding de {p} ≥ {threshold:.0f} %/año durante {needed} comprobaciones seguidas "
                           f"(ahora {apr:.1f} %). Puede compensar el cash and carry: revisa research/funding_study.")
                    print(msg, flush=True)
                    tg.send(msg)
            print(time.strftime("%Y-%m-%d %H:%M"), " · ".join(line), flush=True)
            await asyncio.sleep(every_min * 60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pares", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    ap.add_argument("--umbral", type=float, default=15.0, help="%% anual equivalente")
    ap.add_argument("--seguidas", type=int, default=6)
    ap.add_argument("--cada-min", type=float, default=60.0)
    a = ap.parse_args()
    asyncio.run(main(a.pares, a.umbral, a.seguidas, a.cada_min))
