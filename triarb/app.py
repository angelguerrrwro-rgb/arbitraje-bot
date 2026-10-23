"""Orquestador: arranque, bucle de escaneo, pantalla y resumen final."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path as FsPath

import aiohttp
from rich.console import Console
from rich.live import Live
from rich.logging import RichHandler

from . import hud
from .binance import LIVE_REST, LIVE_WS, TESTNET_REST, TESTNET_WS, BinanceRest, parse_symbols
from .config import Config
from .engine import best_plan, quick_rate
from .executor import ExecResult, Executor
from .journal import Journal
from .market import MarketData
from .models import Plan
from .notify import Telegram
from .paths import build_index, build_paths
from .pricing import UsdPricer
from .risk import Risk
from .venues import LiveVenue, PaperVenue

log = logging.getLogger("triarb")
console = Console()


def setup_logging(cfg: Config) -> None:
    folder = FsPath(cfg.carpeta_registro)
    folder.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    fh = logging.FileHandler(folder / "triarb.log", encoding="utf-8")
    fh.setLevel(getattr(logging, cfg.nivel_registro, logging.INFO))
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(fh)
    ch = RichHandler(console=console, show_path=False, markup=False)
    ch.setLevel(logging.WARNING if cfg.pantalla else logging.INFO)
    ch.addFilter(lambda record: not getattr(record, "file_only", False))
    root.addHandler(ch)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


def say(msg: str) -> None:
    """Mensaje de arranque: a la consola tal cual y al archivo de registro, sin duplicar."""
    console.print(msg)
    log.info(msg, extra={"file_only": True})


class App:
    def __init__(self, cfg: Config, duration: float | None = None):
        self.cfg = cfg
        self.duration = duration
        self.executor: Executor | None = None
        self.risk = Risk(cfg.max_ejecuciones, cfg.max_ejecuciones_por_minuto, cfg.perdida_max_diaria_usd)
        self.plans: dict[int, Plan] = {}
        self.rates: list[float | None] = []
        self.rtt_ms = 0.0
        self.started = time.monotonic()
        self.msgs_per_s = 0.0
        self.busy_pct = 0.0
        self.evals = 0
        self.blocked_by_lag = 0
        self._busy_s = 0.0

    async def run(self) -> None:
        setup_logging(self.cfg)
        for w in self.cfg.warnings:
            log.warning(w)
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            await self._run(session)

    async def _run(self, session: aiohttp.ClientSession) -> None:
        cfg = self.cfg
        testnet = cfg.modo == "testnet"
        rest = self.rest = BinanceRest(session, TESTNET_REST if testnet else LIVE_REST, cfg.api_key, cfg.api_secret)

        say("Midiendo latencia con Binance…")
        self.rtt_ms = await rest.ping_ms()
        await rest.sync_time()
        say(f"Latencia de ida y vuelta: {self.rtt_ms:.0f} ms")
        if abs(rest.time_offset_ms) > 1000:
            log.warning("Tu reloj va %.1f s desfasado respecto a Binance. El bot lo compensa, pero conviene "
                        "sincronizar la hora de Windows (Configuración → Hora → Sincronizar ahora).",
                        rest.time_offset_ms / 1000)

        self.symbols = parse_symbols(await rest.exchange_info())
        self.paths = build_paths(self.symbols, list(cfg.bases), set(cfg.activos) or None, cfg.longitud_max_ruta, cfg.fee_for)
        if not self.paths:
            raise SystemExit("No hay ninguna ruta con esas bases y activos. Revisa [bases] y mercado.activos.")
        self.index = build_index(self.paths)
        self.rates = [None] * len(self.paths)
        watched = sorted(self.index)
        say(f"{len(self.symbols)} pares operativos · {len(watched)} vigilados · {len(self.paths)} rutas desde {', '.join(cfg.bases)}")

        self.market = MarketData(session, TESTNET_WS if testnet else LIVE_WS, watched, cfg.fuente, cfg.niveles,
                                 snapshot=rest.book_tickers, clock_offset_ms=rest.time_offset_ms)
        self.pricer = UsdPricer(self.market.books, await rest.ticker_prices())
        self.journal = Journal(cfg.carpeta_registro, cfg.guardar_oportunidades, cfg.modo, cfg.ejecucion)
        self.telegram = Telegram(session, cfg.telegram_token, cfg.telegram_chat, cfg.telegram)

        venue = None
        if cfg.modo == "papel":
            venue = PaperVenue(
                self.market.books, self._paper_delay_s, cfg.papel_saldos, cfg.fee_for,
                cfg.papel_deslizamiento_pct, unlimited=cfg.ejecucion == "paralela",
            )
            how = f"{cfg.papel_latencia_ms:.0f} ms fijos" if cfg.papel_latencia_ms else "RTT/2 + retraso de datos medido"
            say(f"Simulación: cada orden se casa contra el libro tras {how}; saldos {cfg.papel_saldos}")
        elif cfg.modo in ("testnet", "real"):
            venue = LiveVenue(rest, cfg.modo)
            await venue.refresh()
            self._check_balances(venue)
        if venue is not None:
            self.executor = Executor(
                venue, self.risk, self.pricer, self.symbols, cfg.ejecucion, cfg.si_falla,
                cfg.enfriamiento_ms, on_result=self._on_result,
            )

        say("Conectando websockets…")
        tasks = [asyncio.create_task(self.market.run(), name="mercado")]
        ready = await self.market.wait_ready(20)
        say(f"Libros recibidos: {ready}/{len(watched)}")
        tasks += [
            asyncio.create_task(self._scan_loop(), name="escaneo"),
            asyncio.create_task(self._stats_loop(), name="estadisticas"),
            asyncio.create_task(self._prices_loop(rest), name="precios"),
        ]
        if cfg.pantalla:
            tasks.append(asyncio.create_task(self._hud_loop(), name="pantalla"))
        self.started = time.monotonic()
        self.telegram.send(f"Bot de arbitraje iniciado en modo {cfg.modo}")
        try:
            done, _ = await asyncio.wait(tasks, timeout=self.duration, return_when=asyncio.FIRST_EXCEPTION)
            for t in done:
                if t.exception():
                    raise t.exception()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.executor and self.executor.busy:
                console.print("Esperando a que termine la ejecución en curso…")
                await self.executor.wait_idle(30)
            self.journal.close_all()
            self._summary()
            self.journal.close()

    @property
    def reaction_ms(self) -> float:
        """Desde que algo pasa en Binance hasta que tu orden llega: retraso de datos + RTT/2."""
        lag = self.market.lag_ms
        return self.rtt_ms / 2 + (lag if lag is not None and lag > 0 else self.rtt_ms / 2)

    def _paper_delay_s(self) -> float:
        if self.cfg.papel_latencia_ms:
            return self.cfg.papel_latencia_ms / 1000
        return max(self.rtt_ms, self.reaction_ms) / 1000

    def _check_balances(self, venue: LiveVenue) -> None:
        """Como checkBalances de bmino: no arrancar sin saldo suficiente en las bases."""
        problems = []
        for asset, (lo, _) in self.cfg.bases.items():
            free = venue.free(asset)
            if free < lo:
                problems.append(f"{asset}: tienes {free:.8g} y el mínimo por operación es {lo:g}")
        if problems:
            raise SystemExit("Saldo insuficiente en " + self.cfg.modo + ":\n  - " + "\n  - ".join(problems))
        shown = ", ".join(f"{a} {venue.free(a):.8g}" for a in self.cfg.bases)
        say(f"Saldos disponibles: {shown}")

    async def _scan_loop(self) -> None:
        m = self.market
        while True:
            await m.event.wait()
            m.event.clear()
            t = time.perf_counter()
            affected: set[int] = set()
            for sym in m.take_dirty():
                ids = self.index.get(sym)
                if ids:
                    affected.update(ids)
            now = time.monotonic()
            for pid in affected:
                self._evaluate(pid, now)
            self.evals += len(affected)
            self._busy_s += time.perf_counter() - t
            await asyncio.sleep(0)

    def _evaluate(self, pid: int, now: float) -> None:
        cfg = self.cfg
        path = self.paths[pid]
        books = self.market.books
        rate = quick_rate(path, books)
        self.rates[pid] = rate
        plan = None
        if rate is not None and (rate - 1) * 100 >= cfg.beneficio_min_pct:
            lo, hi = cfg.bases[path.start]
            usd = self.pricer.usd(path.start)
            if usd and cfg.max_por_operacion_usd:
                hi = min(hi, cfg.max_por_operacion_usd / usd)
            if self.executor is not None:
                hi = min(hi, self.executor.venue.free(path.start))
            plan = best_plan(path, books, lo, hi, cfg.tamanos_a_probar)
            if plan is not None:
                plan.profit_usd = plan.profit * usd if usd else None
                plan.age_ms = (now - min(books[s].ts for s in path.symbols)) * 1000
                if plan.profit <= 0 or plan.pct < cfg.beneficio_min_pct or (plan.profit_usd or 0) < cfg.beneficio_min_usd:
                    plan = None
        if plan is None:
            self.plans.pop(pid, None)
            self.journal.update(path, None, now)
            return
        self.plans[pid] = plan
        self.journal.update(path, plan, now)
        ex = self.executor
        if ex is None:
            return
        lag = self.market.lag_ms
        if cfg.retraso_max_ms and (lag is None or lag > cfg.retraso_max_ms):
            self.blocked_by_lag += 1
            return
        fresh = cfg.fuente == "libro" or plan.age_ms <= cfg.edad_max_ms
        if fresh and ex.ready(plan, now):
            log.info("Ejecutando %s: esperado %+.4f %% (%.6f USD) con %.8g %s", path.name, plan.pct,
                     plan.profit_usd or 0, plan.spent, path.start)
            self.journal.mark_executed(pid)
            ex.submit(plan)

    def _on_result(self, res: ExecResult) -> None:
        self.journal.execution(res)
        state = "COMPLETA" if res.success else "FALLIDA"
        msg = (f"{state} {res.plan.path.name}: esperado {res.plan.pct:+.4f} %, PnL {res.pnl_usd:+.6f} USD "
               f"en {res.duration_ms:.0f} ms {res.note}")
        log.info(msg)
        self.telegram.send(f"[{self.cfg.modo}] {msg}")
        if self.risk.stopped_reason:
            log.warning("Ejecución detenida: %s", self.risk.stopped_reason)
            self.telegram.send(f"[{self.cfg.modo}] Ejecución DETENIDA: {self.risk.stopped_reason}")

    async def _stats_loop(self) -> None:
        """Ritmos medios de los últimos 5 s (los mensajes llegan a ráfagas) y un registro por minuto."""
        window = 5
        samples = [(time.monotonic(), self.market.msg_count, self._busy_s)]
        last_log = time.monotonic()
        while True:
            await asyncio.sleep(1)
            samples.append((time.monotonic(), self.market.msg_count, self._busy_s))
            del samples[:-(window + 1)]
            (t0, m0, b0), (t1, m1, b1) = samples[0], samples[-1]
            self.msgs_per_s = (m1 - m0) / (t1 - t0)
            self.busy_pct = (b1 - b0) / (t1 - t0) * 100
            if t1 - last_log >= 60:
                last_log = t1
                lag = self.market.lag_ms
                log.info("Estado: %.0f msg/s, retraso de datos %s ms, CPU de cálculo %.0f %%, %d libros, peso API %d, "
                         "%d oportunidades", self.msgs_per_s, f"{lag:.0f}" if lag is not None else "?",
                         self.busy_pct, len(self.market.books), self.rest.used_weight, self.journal.closed)

    async def _prices_loop(self, rest: BinanceRest) -> None:
        while True:
            await asyncio.sleep(300)
            try:
                self.pricer.snapshot = await rest.ticker_prices()
            except Exception as e:
                log.warning("No se pudieron refrescar los precios en USD: %s", e)

    async def _hud_loop(self) -> None:
        with Live(console=console, auto_refresh=False, transient=False) as live:
            while True:
                live.update(hud.render(self), refresh=True)
                await asyncio.sleep(self.cfg.pantalla_refresco_ms / 1000)

    def _summary(self) -> None:
        elapsed = time.monotonic() - self.started
        j = self.journal
        console.print()
        console.rule("Resumen")
        console.print(f"Tiempo: {elapsed / 60:.1f} min · mensajes de mercado: {self.market.msg_count:,} "
                      f"({self.market.msg_count / max(elapsed, 1):.0f}/s) · reconexiones: {self.market.reconnects}")
        console.print(f"Oportunidades por encima del umbral: {j.closed} · valor potencial sumado: {j.potential_usd:.4f} USD "
                      "(cota superior: varias rutas comparten la misma liquidez)")
        lag = self.market.lag_ms
        reaction = self.reaction_ms
        console.print(f"Latencia: ping {self.rtt_ms:.0f} ms · retraso de los datos "
                      f"{f'{lag:.0f} ms (mediana)' if lag is not None else 'sin medir'} · "
                      f"tiempo de reacción ≈ {reaction:.0f} ms")
        stats = j.duration_stats()
        if stats:
            durations = sorted(j.durations)
            faster = sum(1 for d in durations if d < reaction) / len(durations) * 100
            console.print(f"Duración de las oportunidades: mediana {stats[0]:.0f} ms · p90 {stats[1]:.0f} ms")
            console.print(f"→ El {faster:.0f} % duró menos que tu tiempo de reacción (imposibles de capturar desde aquí).")
        if self.executor:
            r = self.risk
            console.print(f"Ejecuciones: {r.successes} completas de {r.executions} · PnL {r.pnl_total_usd:+.6f} USD · {r.status()}")
            if self.blocked_by_lag:
                console.print(f"Oportunidades no ejecutadas por retraso de datos > {self.cfg.retraso_max_ms} ms: "
                              f"{self.blocked_by_lag} evaluaciones")
            if self.cfg.modo == "papel":
                bal = {a: round(v, 8) for a, v in self.executor.venue.balances.items() if abs(v) > 1e-12}
                console.print(f"Saldos simulados finales: {bal}")
        console.print(f"Registros en {FsPath(self.cfg.carpeta_registro).resolve()}")
