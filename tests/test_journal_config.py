import csv
from pathlib import Path

import pytest

from triarb.config import ConfigError, load_config
from triarb.engine import simulate
from triarb.journal import Journal

ROOT = Path(__file__).resolve().parent.parent


def test_episode_lifecycle(tmp_path, forward, books):
    j = Journal(str(tmp_path), True, "papel", "lineal")
    plan = simulate(forward, 100.0, books)
    plan.profit_usd = plan.profit
    j.update(forward, plan, 10.0)
    j.update(forward, plan, 10.02)
    assert len(j.open) == 1 and j.closed == 0
    j.update(forward, None, 10.05)
    assert j.closed == 1 and not j.open
    assert j.durations[0] == pytest.approx(50.0)
    j.close()
    rows = list(csv.DictReader(open(tmp_path / "oportunidades.csv", encoding="utf-8")))
    assert rows[0]["ruta"] == forward.name and rows[0]["actualizaciones"] == "2"


def test_example_config_is_valid(monkeypatch):
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    cfg = load_config(ROOT / "config.example.toml")
    assert cfg.modo == "papel" and cfg.bases == {"USDT": (10.0, 100.0)}
    assert cfg.fee_for("BTCUSDT") == pytest.approx(0.001)


def test_real_mode_requires_keys(monkeypatch):
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    with pytest.raises(ConfigError, match="BINANCE_API_KEY"):
        load_config(ROOT / "config.example.toml", "real")


def test_invalid_values_are_reported(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text('modo = "papel"\n[bases.USDT]\nmin = 100\nmax = 10\n[estrategia]\nejecucion = "rapida"\n', encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(bad)
    assert "min no puede ser mayor" in str(e.value) and "ejecucion" in str(e.value)
