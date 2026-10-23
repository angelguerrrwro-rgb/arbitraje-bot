"""Graba a la vez el mejor bid/ask de spot y de perpetuos USDⓈ-M de Binance (mismo par).

Sirve para medir la diferencia (base) spot–perpetuo y cuánto tarda en cerrarse.
Archivo: data/basis.bin con filas [hora ms, (spot bid, spot ask, perp bid, perp ask) × pares]
y data/basis_meta.json con la lista de pares y sus cantidades al mejor precio no se guardan
(la capacidad se mira aparte con el volumen).

Uso: python -m research.collect_basis --minutos 20 --intervalo 1.5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from array import array
from pathlib import Path

import aiohttp

OUT = Path(__file__).parent / "data"


async def main(minutes: float, interval: float) -> None:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
        async def get(url):
            async with s.get(url) as r:
                r.raise_for_status()
                return await r.json()

        spot_info = await get("https://api.binance.com/api/v3/exchangeInfo")
        fut_info = await get("https://fapi.binance.com/fapi/v1/exchangeInfo")
        spot = {x["symbol"] for x in spot_info["symbols"] if x["status"] == "TRADING" and x["quoteAsset"] == "USDT"}
        perps = {x["symbol"] for x in fut_info["symbols"]
                 if x["status"] == "TRADING" and x["contractType"] == "PERPETUAL" and x["quoteAsset"] == "USDT"}
        common = sorted(spot & perps)
        vol = {t["symbol"]: float(t["quoteVolume"]) for t in await get("https://fapi.binance.com/fapi/v1/ticker/24hr")}
        idx = {sym: i for i, sym in enumerate(common)}
        (OUT / "basis_meta.json").write_text(json.dumps({"symbols": common, "perp_volume_24h": vol}), encoding="utf-8")
        print(f"{len(common)} pares con spot y perpetuo", flush=True)
        end = time.monotonic() + minutes * 60
        n = 0
        with open(OUT / "basis.bin", "wb") as fh:
            while time.monotonic() < end:
                t0 = time.monotonic()
                try:
                    sp, fu = await asyncio.gather(get("https://api.binance.com/api/v3/ticker/bookTicker"),
                                                  get("https://fapi.binance.com/fapi/v1/ticker/bookTicker"))
                except Exception as e:
                    print("error:", e, flush=True)
                    await asyncio.sleep(2)
                    continue
                row = array("d", [math.nan]) * (1 + 4 * len(common))
                row[0] = time.time() * 1000
                for x in sp:
                    i = idx.get(x["symbol"])
                    if i is not None:
                        row[1 + 4 * i] = float(x["bidPrice"])
                        row[2 + 4 * i] = float(x["askPrice"])
                for x in fu:
                    i = idx.get(x["symbol"])
                    if i is not None:
                        row[3 + 4 * i] = float(x["bidPrice"])
                        row[4 + 4 * i] = float(x["askPrice"])
                row.tofile(fh)
                fh.flush()
                n += 1
                if n % 100 == 0:
                    print(f"{n} fotos", flush=True)
                await asyncio.sleep(max(0.0, interval - (time.monotonic() - t0)))
    print(f"Fin: {n} fotos", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutos", type=float, default=20)
    ap.add_argument("--intervalo", type=float, default=1.5)
    a = ap.parse_args()
    asyncio.run(main(a.minutos, a.intervalo))
