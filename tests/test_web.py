"""Dashboard API — served from a temp state/report dir via SPYQQQ_STATE_DIR / SPYQQQ_REPORT_DIR."""
import importlib
import json
import os
import sys
from pathlib import Path

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    state, reports = tmp_path / "state", tmp_path / "reports"
    monkeypatch.setenv("SPYQQQ_STATE_DIR", str(state))
    monkeypatch.setenv("SPYQQQ_REPORT_DIR", str(reports))
    for m in ("spyqqq.web", "spyqqq.report", "spyqqq.ledger", "spyqqq.config"):
        sys.modules.pop(m, None)
    import spyqqq.config as config
    assert config.STATE_DIR == state and config.REPORT_DIR == reports
    web = importlib.import_module("spyqqq.web")
    web.app.testing = True
    yield web.app.test_client(), config
    for m in ("spyqqq.web", "spyqqq.report", "spyqqq.ledger", "spyqqq.config"):
        sys.modules.pop(m, None)
    monkeypatch.delenv("SPYQQQ_STATE_DIR"); monkeypatch.delenv("SPYQQQ_REPORT_DIR")
    importlib.import_module("spyqqq.config")       # restore the real paths for later tests


def _row(ts, kind, **k):
    return json.dumps({"ts": ts, "mode": "paper", "kind": kind, "sym": k.pop("sym", "SPY"),
                       "setup": k.pop("setup", "vwap_momentum"), **k})


def test_index_serves_dashboard_shell(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200
    html = r.data.decode()
    for anchor in ('id="sym-SPY"', 'id="sym-QQQ"', 'id="timeline"', 'id="activity"', 'id="chart"', "/static/app.js?v="):
        assert anchor in html


def test_status_404_then_passthrough(client):
    c, config = client
    assert c.get("/api/status").status_code == 404
    config.STATUS_PATH.write_text(json.dumps({"mode": "paper", "epoch": 1}))
    r = c.get("/api/status")
    assert r.status_code == 200 and r.get_json()["mode"] == "paper"


def test_ledger_filters_by_day_and_summary_dedupes_closes(client):
    c, config = client
    config.LEDGER_PATH.write_text("\n".join([
        _row("2026-11-16T10:41:00-05:00", "decision", direction="bull", accepted=False, gates=[]),
        _row("2026-11-16T10:42:00-05:00", "fill", pos_id="p1", net_debit=1.6),
        _row("2026-11-16T12:00:00-05:00", "close", pos_id="p1", exit=1.9, pnl_usd=58.0, reason="TP"),
        _row("2026-11-16T12:00:05-05:00", "close", pos_id="p1", exit=1.9, pnl_usd=58.0, reason="TP"),   # duplicate row
        _row("2026-11-17T11:00:00-05:00", "close", pos_id="p2", sym="QQQ", exit=1.2, pnl_usd=-30.0, reason="stop"),
        _row("2026-11-17T11:00:00-05:00", "close", pos_id="boot", exit=None, pnl_usd=0.0, reason="missing at broker on boot"),
    ]) + "\n")
    day = c.get("/api/ledger?day=2026-11-16").get_json()
    assert [r["kind"] for r in day] == ["decision", "fill", "close", "close"]
    s = c.get("/api/summary").get_json()
    assert [p["cum"] for p in s["series"]] == [58.0, 28.0]          # deduped by pos_id; exit=None excluded
    assert s["days"] == ["2026-11-17", "2026-11-16"]
    assert s["by_day"] == [{"day": "2026-11-16", "net": 58.0, "n": 1}, {"day": "2026-11-17", "net": -30.0, "n": 1}]
    assert s["rolling"]["n"] == 2


def test_reports_listing_and_fetch(client):
    c, config = client
    assert c.get("/api/reports").get_json() == []
    assert c.get("/api/report/2026-11-16").status_code == 404
    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (config.REPORT_DIR / "2026-11-16.md").write_text("# hi")
    (config.REPORT_DIR / "2026-11-17.md").write_text("# hi2")
    assert c.get("/api/reports").get_json() == ["2026-11-17", "2026-11-16"]
    assert c.get("/api/report/2026-11-16").data == b"# hi"


def test_config_endpoint_has_no_paths(client):
    c, _ = client
    cfg = c.get("/api/config").get_json()
    assert cfg["PAPER_ONLY"] is True and "STATUS_PATH" not in cfg and "STATE_DIR" not in cfg


@pytest.mark.skipif(not any(os.access(os.path.join(p, "node"), os.X_OK) for p in os.environ.get("PATH", "").split(os.pathsep)), reason="node not installed")
def test_dashboard_js_parses():
    import subprocess
    js = Path(__file__).resolve().parent.parent / "web" / "static" / "app.js"
    assert subprocess.run(["node", "--check", str(js)], capture_output=True).returncode == 0
