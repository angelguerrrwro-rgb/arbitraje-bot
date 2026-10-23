"""Arranque del carry desde la línea de comandos: modo papel 24/7 o backtest."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import aiohttp
from rich.console import Console
from rich.table import Table

from .carry import CANDIDATES, VENUES, Carry, CarryConfig, backtest


def _config(args) -> CarryConfig:
    return CarryConfig(venue=args.plataforma, capital=args.capital, leverage=args.apalancamiento,
                       minimum=args.minimo / 100, folder=args.carpeta, assets=args.activos or [])


async def _backtest(cfg: CarryConfig, days: int) -> None:
    console = Console(width=140)
    cands = [c for c in CANDIDATES[cfg.venue] if not cfg.assets or c.asset in cfg.assets]
    since = int((time.time() - days * 86400) * 1000)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        venue = VENUES[cfg.venue](s)
        history = {c.asset: await venue.funding(c, since) for c in cands}
    for a, rows in history.items():
        if rows:
            yearly = sum(r for _, r in rows) / days * 365 * 100
            pos = sum(r > 0 for _, r in rows) / len(rows) * 100
            console.print(f"  {a:5s}: {len(rows)} pagos · funding medio {yearly:+.2f} %/año · positivos {pos:.0f} %")
    t = Table(title=f"Backtest del carry en {cfg.venue} · {days} días · capital {cfg.capital:.0f} $ · apalancamiento "
                    f"{cfg.leverage:g}x (rentabilidad sobre el capital total, comisiones incluidas)")
    for col in ("Reglas", "Cambios", "Tiempo en cada activo", "Rentabilidad del periodo", "Anualizada",
                f"Con {cfg.capital:.0f} $ al año"):
        t.add_column(col, justify="left" if col == "Reglas" else "right")

    def row(label, c, hist):
        r = backtest(hist, c)
        total = sum(r["held"].values()) or 1
        held = ", ".join(f"{a} {n / total * 100:.0f} %" for a, n in sorted(r["held"].items(), key=lambda x: -x[1]))
        t.add_row(label, str(r["switches"]), held or "fuera", f"{r['return'] * 100:+.2f} %",
                  f"{r['annual'] * 100:+.2f} %", f"{r['annual'] * cfg.capital:+.2f} $")

    for window in (3, 7, 14):
        for switch in (0.05, 0.10, float("inf")):
            c = CarryConfig(**{**cfg.__dict__, "window_days": window, "switch_margin": switch})
            label = (f"ventana {window} d · cambiar si +{switch * 100:.0f} %/año" if switch != float("inf")
                     else f"ventana {window} d · no cambiar nunca")
            row(label, c, history)
    for a in history:  # referencia: quedarse siempre en un activo
        c = CarryConfig(**{**cfg.__dict__, "minimum": -1.0, "exit_below": -1.0, "switch_margin": float("inf")})
        row(f"siempre en {a}", c, {a: history[a]})
    console.print(t)
    console.print("El umbral se elige con los mismos datos: tómalo como orientación, no como promesa.")


def run_carry(args) -> int:
    cfg = _config(args)
    Path(cfg.folder).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        handlers=[logging.FileHandler(Path(cfg.folder) / "carry.log", encoding="utf-8")])
    if args.backtest:
        asyncio.run(_backtest(cfg, args.backtest))
        return 0

    async def main():
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            bot = Carry(cfg, s)
            print(f"Carry en modo PAPEL · {cfg.venue} · candidatos {', '.join(bot.cands)} · capital simulado "
                  f"{bot.book.equity(None):.2f} $ · estado en {bot.state_path}", flush=True)
            await bot.run(args.minutos * 60 if args.minutos else None)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    return 0
