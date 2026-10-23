"""Punto de entrada: `python -m triarb` (bot) y `python -m triarb analizar` (estadísticas)."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from .config import MODES, ConfigError, load_config, load_env


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(prog="python -m triarb", description="Bot de arbitraje triangular para Binance Spot")
    parser.add_argument("--config", default="config.toml", help="archivo de configuración (por defecto config.toml)")
    parser.add_argument("--modo", choices=MODES, help="sobrescribe el modo del archivo")
    parser.add_argument("--sin-pantalla", action="store_true", help="sin tabla en vivo, solo registro")
    parser.add_argument("--duracion", type=float, metavar="SEG", help="parar tras SEG segundos")
    parser.add_argument("--confirmo-dinero-real", action="store_true", help="obligatorio para el modo real")
    sub = parser.add_subparsers(dest="cmd")
    an = sub.add_parser("analizar", help="estadísticas de logs/oportunidades.csv y logs/ejecuciones.csv")
    an.add_argument("--carpeta", default="logs")
    ca = sub.add_parser("carry", help="carry spot + perpetuo en una plataforma (modo papel, 24/7)")
    ca.add_argument("--plataforma", choices=["binance", "bybit"], default="binance")
    ca.add_argument("--capital", type=float, default=500.0)
    ca.add_argument("--apalancamiento", type=float, default=2.0, help="del corto (2 = se liquida con ~+49 %%)")
    ca.add_argument("--activos", nargs="+", help="limitar candidatos, p. ej. PAXG XAUT")
    ca.add_argument("--minimo", type=float, default=4.0, help="%% anual de funding para entrar")
    ca.add_argument("--backtest", type=int, metavar="DIAS", help="probar las reglas con el funding real de DIAS días")
    ca.add_argument("--minutos", type=float, help="parar tras N minutos (por defecto, sin fin)")
    ca.add_argument("--carpeta", default="logs")
    args = parser.parse_args(argv)

    if args.cmd == "analizar":
        from .analyze import analyze

        analyze(args.carpeta)
        return 0
    if args.cmd == "carry":
        from .carry_cli import run_carry

        return run_carry(args)

    load_env()
    try:
        cfg = load_config(args.config, args.modo)
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    if args.sin_pantalla:
        cfg.pantalla = False

    if cfg.modo == "real":
        if not args.confirmo_dinero_real:
            print("El modo real opera con tu dinero. Añade --confirmo-dinero-real si de verdad quieres hacerlo.", file=sys.stderr)
            return 2
        print("⚠  MODO REAL: se enviarán órdenes con dinero real. Ctrl+C en 5 s para cancelar…")
        time.sleep(5)

    from .app import App

    try:
        asyncio.run(App(cfg, args.duracion).run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
