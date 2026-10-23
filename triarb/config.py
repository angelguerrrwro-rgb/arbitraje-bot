"""Carga y validación de config.toml y .env (la validación exhaustiva viene de bmino)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

MODES = ("escanear", "papel", "testnet", "real")

DEFAULT_ASSETS = [
    "USDT", "USDC", "FDUSD", "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "TRX", "LINK",
    "LTC", "DOT", "AVAX", "BCH", "SHIB", "PEPE", "SUI", "TON", "NEAR", "APT", "ARB", "OP",
    "UNI", "ATOM", "ETC", "FIL", "EUR",
]


class ConfigError(Exception):
    pass


@dataclass
class Config:
    modo: str = "papel"
    activos: list[str] = field(default_factory=lambda: list(DEFAULT_ASSETS))
    fuente: str = "libro"
    niveles: int = 10
    longitud_max_ruta: int = 3
    bases: dict[str, tuple[float, float]] = field(default_factory=lambda: {"USDT": (10.0, 100.0)})
    comision_pct: float = 0.1
    comisiones_por_par: dict[str, float] = field(default_factory=dict)
    beneficio_min_pct: float = 0.0
    beneficio_min_usd: float = 0.0
    edad_max_ms: int = 300
    retraso_max_ms: int = 1000
    ejecucion: str = "lineal"
    si_falla: str = "deshacer"
    enfriamiento_ms: int = 2000
    tamanos_a_probar: int = 6
    max_ejecuciones: int = 1
    max_ejecuciones_por_minuto: int = 6
    perdida_max_diaria_usd: float = 5.0
    max_por_operacion_usd: float = 100.0
    papel_latencia_ms: float = 0.0
    papel_saldos: dict[str, float] = field(default_factory=lambda: {"USDT": 1000.0})
    papel_deslizamiento_pct: float = 0.05
    pantalla: bool = True
    pantalla_filas: int = 15
    pantalla_refresco_ms: int = 500
    carpeta_registro: str = "logs"
    nivel_registro: str = "INFO"
    guardar_oportunidades: bool = True
    telegram: bool = False
    api_key: str = ""
    api_secret: str = ""
    telegram_token: str = ""
    telegram_chat: str = ""
    warnings: list[str] = field(default_factory=list)

    def fee_for(self, symbol: str) -> float:
        return self.comisiones_por_par.get(symbol, self.comision_pct) / 100.0


def load_env(path: str | Path = ".env") -> None:
    """Lee KEY=VALOR de .env sin pisar variables ya definidas."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _section(raw: dict, name: str) -> dict:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] debe ser una sección")
    return value


def load_config(path: str | Path, mode_override: str | None = None) -> Config:
    p = Path(path)
    raw: dict = {}
    if p.exists():
        with open(p, "rb") as fh:
            raw = tomllib.load(fh)
    cfg = Config()
    cfg.modo = mode_override or raw.get("modo", cfg.modo)

    m = _section(raw, "mercado")
    cfg.activos = [a.upper() for a in m.get("activos", cfg.activos)]
    cfg.fuente = m.get("fuente", cfg.fuente)
    cfg.niveles = m.get("niveles", cfg.niveles)
    cfg.longitud_max_ruta = m.get("longitud_max_ruta", cfg.longitud_max_ruta)

    if "bases" in raw:
        cfg.bases = {}
        for asset, b in _section(raw, "bases").items():
            if not isinstance(b, dict) or "min" not in b or "max" not in b:
                raise ConfigError(f"[bases.{asset}] necesita min y max")
            cfg.bases[asset] = (float(b["min"]), float(b["max"]))

    e = _section(raw, "estrategia")
    cfg.comision_pct = float(e.get("comision_pct", cfg.comision_pct))
    cfg.comisiones_por_par = {k.upper(): float(v) for k, v in e.get("comisiones_por_par", {}).items()}
    cfg.beneficio_min_pct = float(e.get("beneficio_min_pct", cfg.beneficio_min_pct))
    cfg.beneficio_min_usd = float(e.get("beneficio_min_usd", cfg.beneficio_min_usd))
    cfg.edad_max_ms = e.get("edad_max_ms", cfg.edad_max_ms)
    cfg.retraso_max_ms = e.get("retraso_max_ms", cfg.retraso_max_ms)
    cfg.ejecucion = e.get("ejecucion", cfg.ejecucion)
    cfg.si_falla = e.get("si_falla_un_tramo", cfg.si_falla)
    cfg.enfriamiento_ms = e.get("enfriamiento_ms", cfg.enfriamiento_ms)
    cfg.tamanos_a_probar = e.get("tamanos_a_probar", cfg.tamanos_a_probar)

    r = _section(raw, "riesgo")
    cfg.max_ejecuciones = r.get("max_ejecuciones", cfg.max_ejecuciones)
    cfg.max_ejecuciones_por_minuto = r.get("max_ejecuciones_por_minuto", cfg.max_ejecuciones_por_minuto)
    cfg.perdida_max_diaria_usd = float(r.get("perdida_max_diaria_usd", cfg.perdida_max_diaria_usd))
    cfg.max_por_operacion_usd = float(r.get("max_por_operacion_usd", cfg.max_por_operacion_usd))

    pa = _section(raw, "papel")
    cfg.papel_latencia_ms = float(pa.get("latencia_ms", cfg.papel_latencia_ms))
    cfg.papel_saldos = {k.upper(): float(v) for k, v in pa.get("saldos", cfg.papel_saldos).items()}
    cfg.papel_deslizamiento_pct = float(pa.get("deslizamiento_mercado_pct", cfg.papel_deslizamiento_pct))

    h = _section(raw, "pantalla")
    cfg.pantalla = h.get("activa", cfg.pantalla)
    cfg.pantalla_filas = h.get("filas", cfg.pantalla_filas)
    cfg.pantalla_refresco_ms = h.get("refresco_ms", cfg.pantalla_refresco_ms)

    lg = _section(raw, "registro")
    cfg.carpeta_registro = lg.get("carpeta", cfg.carpeta_registro)
    cfg.nivel_registro = str(lg.get("nivel", cfg.nivel_registro)).upper()
    cfg.guardar_oportunidades = lg.get("guardar_oportunidades", cfg.guardar_oportunidades)

    cfg.telegram = _section(raw, "telegram").get("activo", cfg.telegram)

    prefix = "BINANCE_TESTNET_" if cfg.modo == "testnet" else "BINANCE_"
    cfg.api_key = os.environ.get(prefix + "API_KEY", "")
    cfg.api_secret = os.environ.get(prefix + "API_SECRET", "")
    cfg.telegram_token = os.environ.get("TELEGRAM_TOKEN", "")
    cfg.telegram_chat = os.environ.get("TELEGRAM_CHAT_ID", "")

    validate(cfg)
    return cfg


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate(cfg: Config) -> None:
    errors: list[str] = []
    warn = cfg.warnings

    if cfg.modo not in MODES:
        errors.append(f"modo debe ser uno de {', '.join(MODES)}")
    if cfg.fuente not in ("libro", "profundidad"):
        errors.append('mercado.fuente debe ser "libro" o "profundidad"')
    if cfg.niveles not in (5, 10, 20):
        errors.append("mercado.niveles debe ser 5, 10 o 20")
    if cfg.longitud_max_ruta not in (3, 4):
        errors.append("mercado.longitud_max_ruta debe ser 3 o 4")
    elif cfg.longitud_max_ruta == 4 and not cfg.activos:
        errors.append("longitud_max_ruta = 4 sin lista de activos genera demasiadas rutas")
    if not cfg.activos:
        warn.append("Sin lista de activos se vigilan todos los pares: mucha más CPU")

    if not cfg.bases:
        errors.append("hace falta al menos una base en [bases]")
    for asset, (lo, hi) in cfg.bases.items():
        if asset != asset.upper() or asset != asset.strip():
            errors.append(f"bases.{asset}: el activo va en mayúsculas y sin espacios")
        if lo <= 0 or hi <= 0:
            errors.append(f"bases.{asset}: min y max deben ser positivos")
        if lo > hi:
            errors.append(f"bases.{asset}: min no puede ser mayor que max")

    if not _is_num(cfg.comision_pct) or cfg.comision_pct < 0:
        errors.append("estrategia.comision_pct debe ser un número positivo")
    elif cfg.comision_pct == 0:
        warn.append("comision_pct = 0 seguramente es un error")
    if cfg.ejecucion not in ("lineal", "paralela"):
        errors.append('estrategia.ejecucion debe ser "lineal" o "paralela"')
    if cfg.si_falla not in ("deshacer", "mantener"):
        errors.append('estrategia.si_falla_un_tramo debe ser "deshacer" o "mantener"')
    if not isinstance(cfg.tamanos_a_probar, int) or cfg.tamanos_a_probar < 1:
        errors.append("estrategia.tamanos_a_probar debe ser un entero ≥ 1")
    if not isinstance(cfg.edad_max_ms, int) or cfg.edad_max_ms <= 0:
        errors.append("estrategia.edad_max_ms debe ser un entero positivo")
    if not isinstance(cfg.retraso_max_ms, int) or cfg.retraso_max_ms < 0:
        errors.append("estrategia.retraso_max_ms debe ser un entero ≥ 0 (0 = sin límite)")
    elif cfg.retraso_max_ms == 0 and cfg.modo == "real":
        warn.append("modo real sin límite de retraso de datos (estrategia.retraso_max_ms = 0)")
    if not isinstance(cfg.enfriamiento_ms, int) or cfg.enfriamiento_ms < 0:
        errors.append("estrategia.enfriamiento_ms debe ser un entero ≥ 0")

    for name in ("max_ejecuciones", "max_ejecuciones_por_minuto"):
        v = getattr(cfg, name)
        if not isinstance(v, int) or v < 0:
            errors.append(f"riesgo.{name} debe ser un entero ≥ 0 (0 = sin límite)")
    if cfg.perdida_max_diaria_usd < 0 or cfg.max_por_operacion_usd < 0:
        errors.append("los límites de riesgo en USD no pueden ser negativos")

    if cfg.modo in ("testnet", "real") and (not cfg.api_key or not cfg.api_secret):
        var = "BINANCE_TESTNET_API_KEY/SECRET" if cfg.modo == "testnet" else "BINANCE_API_KEY/SECRET"
        errors.append(f"el modo {cfg.modo} necesita {var} en .env")
    if cfg.modo == "real":
        if cfg.max_ejecuciones == 0:
            warn.append("modo real sin tope de ejecuciones (riesgo.max_ejecuciones = 0)")
        if cfg.perdida_max_diaria_usd == 0:
            warn.append("modo real sin límite de pérdida diaria")
    if cfg.modo == "papel" and cfg.ejecucion == "lineal":
        for asset in cfg.bases:
            if cfg.papel_saldos.get(asset, 0) <= 0:
                warn.append(f"papel.saldos no tiene saldo de {asset}: no se simulará ninguna operación desde esa base")

    if errors:
        raise ConfigError("Configuración inválida:\n  - " + "\n  - ".join(errors))
