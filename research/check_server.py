"""¿Sirve este servidor para el bot? Ejecútalo en la máquina candidata antes de nada.

Comprueba en ~1 minuto:
1. Si Binance bloquea la IP (HTTP 451/403 "restricted location"), en spot y en futuros.
2. Ping REST y desfase del reloj.
3. El websocket con ~120 pares: si el retraso de los datos se mantiene estable o se acumula
   (lo que pasaba en el PC de casa: de 0,5 s a 25 s en 30 s).

Uso: python -m research.check_server
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time

import aiohttp

from triarb.binance import LIVE_REST, LIVE_WS, BinanceError, BinanceRest, parse_symbols
from triarb.config import DEFAULT_ASSETS
from triarb.paths import build_index, build_paths


def verdict(ok: bool, text: str) -> None:
    print(("  OK   " if ok else "  MAL  ") + text, flush=True)


async def ws_test(session, streams: list[str], offset_ms: float, seconds: int = 40) -> tuple[list[float], float, int]:
    windows, current, nbytes, n = [], [], 0, 0
    async with session.ws_connect(LIVE_WS, max_msg_size=0) as ws:
        for i in range(0, len(streams), 100):
            await ws.send_str(json.dumps({"method": "SUBSCRIBE", "params": streams[i : i + 100], "id": i + 1}))
            await asyncio.sleep(0.3)
        start = time.monotonic()
        mark = start + 5
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                break
            nbytes += len(msg.data)
            n += 1
            d = json.loads(msg.data).get("data") or {}
            if "E" in d:
                current.append(time.time() * 1000 + offset_ms - d["E"])
            now = time.monotonic()
            if now >= mark:
                windows.append(statistics.median(current) if current else float("nan"))
                current, mark = [], mark + 5
            if now - start >= seconds:
                break
    return windows, nbytes / seconds / 1024, n


async def main() -> None:
    print("Comprobando si este servidor sirve para operar en Binance…\n", flush=True)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        rest = BinanceRest(s, LIVE_REST)
        try:
            rtt = await rest.ping_ms()
            await rest.sync_time()
        except BinanceError as e:
            verdict(False, f"Binance spot rechaza esta IP: {e}")
            if e.status in (451, 403):
                print("\n→ Este servidor NO sirve: Binance bloquea su ubicación o su rango de IPs.")
            return
        verdict(True, "Binance spot responde")
        verdict(rtt < 50, f"Ping REST: {rtt:.0f} ms (ideal < 10 ms en Tokio; < 50 ms aceptable; en casa medimos 260 ms)")
        verdict(abs(rest.time_offset_ms) < 1000, f"Desfase de reloj: {rest.time_offset_ms:.0f} ms")

        try:
            async with s.get("https://fapi.binance.com/fapi/v1/ping") as r:
                verdict(r.status == 200, f"Binance futuros: HTTP {r.status}")
        except Exception as e:
            verdict(False, f"Binance futuros no responde: {e}")

        symbols = parse_symbols(await rest.exchange_info())
        watched = sorted(build_index(build_paths(symbols, ["USDT"], set(DEFAULT_ASSETS), 3, lambda x: 0.001)))
        streams = ["btcusdt@trade"] + [f"{x.lower()}@bookTicker" for x in watched]
        print(f"\nWebsocket con {len(streams)} streams durante 40 s…", flush=True)
        windows, kbps, n = await ws_test(s, streams, rest.time_offset_ms)
        shown = " → ".join(f"{w:.0f}" for w in windows)
        print(f"  Retraso mediano cada 5 s (ms): {shown}")
        growing = len(windows) >= 4 and windows[-1] > windows[0] + 500
        verdict(not growing, "El retraso no se acumula" if not growing else "El retraso SE ACUMULA (como en casa)")
        final = windows[-1] if windows else float("nan")
        verdict(final < 100, f"Retraso final de los datos: {final:.0f} ms · {n / 40:.0f} msg/s · {kbps:.1f} KB/s")

        good = rtt < 50 and not growing and final < 100
        print("\n→ " + ("Este servidor sirve para el bot." if good else
                        "Este servidor no es adecuado para arbitraje (latencia o retraso de datos demasiado altos)."))


if __name__ == "__main__":
    asyncio.run(main())
