"""Graba fotos del mejor bid/ask de TODOS los pares por REST (una petición por foto).

Cada foto sale de Binance de una vez, así que es coherente entre pares: no sufre los
retrasos ni las ráfagas del websocket. Sirve para estudiar el arbitraje, no para operar.

Uso: python -m research.collect_books --minutos 20 --intervalo 1
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

from triarb.binance import LIVE_REST, BinanceRest, parse_symbols

OUT = Path(__file__).parent / "data"
HEADER = 2  # [hora Binance en ms, duración de la petición en ms]


async def main(minutes: float, interval: float) -> None:
    OUT.mkdir(exist_ok=True)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        rest = BinanceRest(session, LIVE_REST)
        await rest.sync_time()
        symbols = parse_symbols(await rest.exchange_info())
        names = sorted(symbols)
        index = {n: i for i, n in enumerate(names)}
        tickers = await rest.request("GET", "/api/v3/ticker/24hr")
        meta = {
            "symbols": [
                {"symbol": n, "base": symbols[n].base, "quote": symbols[n].quote, "step": symbols[n].step,
                 "min_qty": symbols[n].min_qty, "min_notional": symbols[n].min_notional}
                for n in names
            ],
            "quote_volume_24h": {t["symbol"]: float(t["quoteVolume"]) for t in tickers if t["symbol"] in index},
            "header": HEADER,
            "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        (OUT / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        width = HEADER + 4 * len(names)
        count = errors = 0
        end = time.monotonic() + minutes * 60
        with open(OUT / "books.bin", "wb") as fh:
            while time.monotonic() < end:
                t0 = time.monotonic()
                try:
                    data = await rest.book_tickers()
                except Exception as e:
                    errors += 1
                    print("error:", e, flush=True)
                    await asyncio.sleep(2)
                    continue
                took = (time.monotonic() - t0) * 1000
                row = array("d", [math.nan]) * width
                row[0] = time.time() * 1000 + rest.time_offset_ms - took / 2
                row[1] = took
                for d in data:
                    i = index.get(d["symbol"])
                    if i is not None:
                        j = HEADER + 4 * i
                        row[j : j + 4] = array("d", (float(d["bidPrice"]), float(d["bidQty"]),
                                                     float(d["askPrice"]), float(d["askQty"])))
                row.tofile(fh)
                fh.flush()
                count += 1
                if count % 60 == 0:
                    print(f"{count} fotos, última petición {took:.0f} ms, peso API {rest.used_weight}", flush=True)
                await asyncio.sleep(max(0.0, interval - (time.monotonic() - t0)))
    print(f"Fin: {count} fotos de {len(names)} pares, {errors} errores", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutos", type=float, default=20)
    ap.add_argument("--intervalo", type=float, default=1.0)
    a = ap.parse_args()
    asyncio.run(main(a.minutos, a.intervalo))
