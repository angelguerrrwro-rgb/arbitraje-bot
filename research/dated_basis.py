"""Cash and carry con futuros CON VENCIMIENTO, en una sola plataforma.

Compras el activo en spot y vendes el futuro (que cotiza más caro). Al vencer, el futuro se
liquida al precio spot: la diferencia de hoy queda asegurada pase lo que pase con el precio.
Rentabilidad anual = (futuro − spot − comisiones) / capital × 365 / días hasta el vencimiento.

Mira ahora mismo todos los futuros con vencimiento de Binance (USDⓈ-M), Bybit y OKX.

Uso: python -m research.dated_basis
"""

from __future__ import annotations

import asyncio
import time

import aiohttp
from rich.console import Console
from rich.table import Table

SPOT_FEE = 0.00075  # spot taker con BNB (Binance); 0,1 % en Bybit/OKX
FUT_FEE = 0.0005
LEVERAGE = 2  # margen del corto = nocional/2 → capital = 1,5 × nocional


async def main() -> None:
    console = Console(width=150)
    rows = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async def get(url, **params):
            async with s.get(url, params=params or None) as r:
                r.raise_for_status()
                return await r.json(content_type=None)

        now = time.time() * 1000
        # Binance USDⓈ-M trimestrales
        info = await get("https://fapi.binance.com/fapi/v1/exchangeInfo")
        dated = [x for x in info["symbols"] if x["contractType"] in ("CURRENT_QUARTER", "NEXT_QUARTER") and x["status"] == "TRADING"]
        fut_bt = {x["symbol"]: x for x in await get("https://fapi.binance.com/fapi/v1/ticker/bookTicker")}
        spot_bt = {x["symbol"]: x for x in await get("https://api.binance.com/api/v3/ticker/bookTicker")}
        for x in dated:
            spot = spot_bt.get(x["pair"])
            fut = fut_bt.get(x["symbol"])
            if spot and fut:
                rows.append(("binance", x["symbol"], float(spot["askPrice"]), float(fut["bidPrice"]),
                             (x["deliveryDate"] - now) / 86_400_000, SPOT_FEE))
        # Bybit futuros con fecha (USDT)
        inst = await get("https://api.bybit.com/v5/market/instruments-info", category="linear", limit=1000)
        tick = {x["symbol"]: x for x in (await get("https://api.bybit.com/v5/market/tickers", category="linear"))["result"]["list"]}
        sp = {x["symbol"]: x for x in (await get("https://api.bybit.com/v5/market/tickers", category="spot"))["result"]["list"]}
        for x in inst["result"]["list"]:
            if x.get("contractType") != "LinearFutures" or x.get("status") != "Trading":
                continue
            spot = sp.get(x["baseCoin"] + "USDT")
            fut = tick.get(x["symbol"])
            if spot and fut and fut.get("bid1Price"):
                rows.append(("bybit", x["symbol"], float(spot["ask1Price"]), float(fut["bid1Price"]),
                             (int(x["deliveryTime"]) - now) / 86_400_000, 0.001))
        # OKX futuros (lineales en USDT)
        inst = await get("https://www.okx.com/api/v5/public/instruments", instType="FUTURES")
        ft = {x["instId"]: x for x in (await get("https://www.okx.com/api/v5/market/tickers", instType="FUTURES"))["data"]}
        st = {x["instId"]: x for x in (await get("https://www.okx.com/api/v5/market/tickers", instType="SPOT"))["data"]}
        for x in inst["data"]:
            if x.get("settleCcy") != "USDT" or x.get("state") != "live":
                continue
            base = x["instFamily"].split("-")[0]
            spot = st.get(f"{base}-USDT")
            fut = ft.get(x["instId"])
            if spot and fut and fut.get("bidPx") and spot.get("askPx"):
                rows.append(("okx", x["instId"], float(spot["askPx"]), float(fut["bidPx"]),
                             (int(x["expTime"]) - now) / 86_400_000, 0.001))

    t = Table(title="Futuros con vencimiento: rentabilidad que se puede fijar HOY (comprar spot + vender futuro)")
    for col in ("Exchange", "Contrato", "Días", "Spot", "Futuro", "Prima", "Neto tras comisiones", "Anual sobre capital"):
        t.add_column(col, justify="left" if col in ("Exchange", "Contrato") else "right")
    out = []
    for ex, sym, spot, fut, days, spot_fee in rows:
        if days < 2 or spot <= 0:
            continue
        premium = fut / spot - 1
        # entrar: spot taker + futuro taker; salir: vender spot al vencimiento (taker) — el futuro liquida solo
        net = premium - 2 * spot_fee - FUT_FEE
        apr = net / (1 + 1 / LEVERAGE) * 365 / days * 100
        out.append((apr, ex, sym, days, spot, fut, premium, net))
    out.sort(reverse=True)
    for apr, ex, sym, days, spot, fut, premium, net in out:
        t.add_row(ex, sym, f"{days:.0f}", f"{spot:,.4g}", f"{fut:,.4g}", f"{premium * 100:+.3f} %", f"{net * 100:+.3f} %",
                  f"[{'green' if apr > 4 else 'yellow' if apr > 0 else 'red'}]{apr:+.1f} %[/]")
    console.print(t)


if __name__ == "__main__":
    asyncio.run(main())
