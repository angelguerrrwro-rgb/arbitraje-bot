"""Graba a la vez el mejor bid/ask de todos los pares de varios exchanges.

Un archivo binario por exchange (data/multi_<exchange>.bin), con filas
[hora local en ms, duración de la petición en ms, (bid, bid_qty, ask, ask_qty) × pares].

Uso: python -m research.collect_multi --minutos 20 --intervalo 3
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

from research.venues import ALL

OUT = Path(__file__).parent / "data"


async def main(minutes: float, interval: float) -> None:
    OUT.mkdir(exist_ok=True)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
        markets = {}
        for v in ALL:
            try:
                markets[v.name] = await v.markets(s)
                print(f"{v.name}: {len(markets[v.name])} pares", flush=True)
            except Exception as e:
                print(f"{v.name}: sin pares ({e!r})", flush=True)
        venues = [v for v in ALL if markets.get(v.name)]
        order = {v.name: sorted(markets[v.name]) for v in venues}
        index = {name: {sym: i for i, sym in enumerate(syms)} for name, syms in order.items()}
        meta = {name: [[sym, *markets[name][sym]] for sym in syms] for name, syms in order.items()}
        (OUT / "multi_meta.json").write_text(json.dumps(meta), encoding="utf-8")
        files = {v.name: open(OUT / f"multi_{v.name}.bin", "wb") for v in venues}
        counts = {v.name: 0 for v in venues}

        async def one(v):
            t0 = time.monotonic()
            try:
                data = await v.tickers(s)
            except Exception as e:
                return v.name, None, str(e)
            took = (time.monotonic() - t0) * 1000
            idx = index[v.name]
            row = array("d", [math.nan]) * (2 + 4 * len(idx))
            row[0] = time.time() * 1000 - took / 2
            row[1] = took
            for sym, vals in data.items():
                i = idx.get(sym)
                if i is not None:
                    row[2 + 4 * i : 6 + 4 * i] = array("d", vals)
            return v.name, row, None

        end = time.monotonic() + minutes * 60
        loops = 0
        while time.monotonic() < end:
            t0 = time.monotonic()
            for name, row, err in await asyncio.gather(*(one(v) for v in venues)):
                if row is None:
                    print(f"{name}: error {err}", flush=True)
                    continue
                row.tofile(files[name])
                files[name].flush()
                counts[name] += 1
            loops += 1
            if loops % 20 == 0:
                print(f"{loops} rondas · " + " ".join(f"{k}:{c}" for k, c in counts.items()), flush=True)
            await asyncio.sleep(max(0.0, interval - (time.monotonic() - t0)))
        for fh in files.values():
            fh.close()
    print("Fin: " + " ".join(f"{k}:{c}" for k, c in counts.items()), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutos", type=float, default=20)
    ap.add_argument("--intervalo", type=float, default=3.0)
    a = ap.parse_args()
    asyncio.run(main(a.minutos, a.intervalo))
