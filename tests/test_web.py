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
    monkeypatch.delenv("SPYQQQ_DASHBOARD_PASSWORD_HASH", raising=False)   # the real .env has one; auth tests set their own
    import spyqqq
    for m in ("spyqqq.web", "spyqqq.report", "spyqqq.ledger", "spyqqq.chart", "spyqqq.config"):
        sys.modules.pop(m, None)
        spyqqq.__dict__.pop(m.split(".")[1], None)   # `from . import chart` reads the package attribute first
    import spyqqq.config as config
    assert config.STATE_DIR == state and config.REPORT_DIR == reports
    web = importlib.import_module("spyqqq.web")
    web.app.testing = True
    yield web.app.test_client(), config
    for m in ("spyqqq.web", "spyqqq.report", "spyqqq.ledger", "spyqqq.chart", "spyqqq.config"):
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
    static = Path(__file__).resolve().parent.parent / "web" / "static"
    for name in ("app.js", "chart.js"):
        assert subprocess.run(["node", "--check", str(static / name)], capture_output=True).returncode == 0, name


# ── chart / why endpoints ────────────────────────────────────────────────────
def _write_bars(config, sym, days):
    """`days` sessions of flat-ish 5-min bars ending yesterday, plus daily closes."""
    from datetime import datetime, timedelta
    import pytz
    et = pytz.timezone("America/New_York")
    config.BARS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(et).date()
    sessions, d = [], today - timedelta(days=1)
    while len(sessions) < days:
        if d.weekday() < 5:
            sessions.append(d)
        d -= timedelta(days=1)
    sessions.reverse()
    with (config.BARS_CACHE_DIR / f"{sym}_5m.jsonl").open("w") as f:
        for k, day in enumerate(sessions):
            t0 = et.localize(datetime(day.year, day.month, day.day, 9, 30))
            for i in range(78):
                px = 500 + k + i * 0.01
                f.write(json.dumps({"ts": (t0 + timedelta(minutes=5 * i)).isoformat(), "open": px, "high": px + .05,
                                    "low": px - .05, "close": px + .02, "volume": 1e6}) + "\n")
    with (config.BARS_CACHE_DIR / f"{sym}_1d.jsonl").open("w") as f:
        for k in range(30):
            f.write(json.dumps({"ts": (today - timedelta(days=60 - k)).isoformat(), "open": 1, "high": 1, "low": 1,
                                "close": 400 + k, "volume": 1}) + "\n")
    return sessions


def test_chart_past_session_and_validation(client):
    c, config = client
    sessions = _write_bars(config, "SPY", 3)
    r = c.get(f"/api/chart/SPY?day={sessions[-1].isoformat()}")
    assert r.status_code == 200
    d = r.get_json()
    assert d["source"] == "cache" and len(d["bars"]) == 78 and d["trend"] == "bull"
    assert d["window"]["start"] < d["window"]["end"]
    assert all({"traded", "provisional", "in_window"} <= set(s) for s in d["signals"])
    assert c.get("/api/chart/AAPL").status_code == 400                      # only the traded underlyings
    assert c.get("/api/chart/SPY?day=not-a-date").status_code == 400
    days = c.get("/api/chart/SPY/days").get_json()
    assert days[0] >= sessions[-1].isoformat() and sessions[0].isoformat() in days


def test_chart_today_falls_back_when_no_live_bars(client, monkeypatch):
    from datetime import datetime
    import pytz
    c, config = client
    sessions = _write_bars(config, "SPY", 3)
    import spyqqq.chart as chart
    monkeypatch.setattr(chart, "_today_bars", lambda sym: [])
    d = c.get("/api/chart/SPY").get_json()
    now = datetime.now(pytz.timezone("America/New_York"))
    if now.weekday() >= 5 or now.strftime("%H:%M") >= "09:45":
        assert d["fallback"] is True and d["day"] == sessions[-1].isoformat() and len(d["bars"]) == 78
    else:
        assert d["fallback"] is False and d["bars"] == []
    w = c.get("/api/why/SPY").get_json()
    assert w["sym"] == "SPY" and (w["live"] is False or w["bar"] is None)


def test_why_lists_every_condition_for_live_bars(client, monkeypatch):
    c, config = client
    sessions = _write_bars(config, "SPY", 3)
    import spyqqq.chart as chart
    from datetime import datetime, timedelta
    import pytz
    et = pytz.timezone("America/New_York")
    t0 = et.localize(datetime.combine(datetime.now(et).date(), datetime.min.time()).replace(hour=9, minute=30))
    from spyqqq import signal as sig
    bars = [sig.Bar(t0 + timedelta(minutes=5 * i), 510 + i * .01, 510.1 + i * .01, 509.9 + i * .01, 510.05 + i * .01, 1e6)
            for i in range(20)]
    monkeypatch.setattr(chart, "_today_bars", lambda sym: bars)
    w = c.get("/api/why/SPY").get_json()
    assert w["live"] is True and w["bar"] == bars[-1].ts.strftime("%H:%M") and w["trend"] == "bull"
    by = {b["setup"]: b for b in w["setups"]}
    assert set(by) == set(config.SETUPS_ENABLED) | {"trend_pullback", "vwap_momentum"}
    vm = by["vwap_momentum"]["directions"][0]
    assert vm["direction"] == "bull" and vm["total"] == 6 and 0 <= vm["passed"] <= 6
    assert all({"label", "ok", "value", "need"} <= set(ck) for ck in vm["checks"])


def test_ledger_api_sorts_by_timestamp_not_file_order(client):
    c, config = client
    config.LEDGER_PATH.write_text("\n".join([
        _row("2026-11-17T11:39:58-05:00", "engine", sym="-", setup="engine", event="shutdown"),
        _row("2026-11-17T11:31:07-05:00", "engine", sym="-", setup="engine", event="boot"),
    ]) + "\n")
    rows = c.get("/api/ledger?day=2026-11-17").get_json()
    assert [r["event"] for r in rows] == ["boot", "shutdown"]


def _hash(pw):
    from werkzeug.security import generate_password_hash
    return generate_password_hash(pw)


def test_no_password_local_ok_remote_refused(client, monkeypatch):
    c, _ = client
    monkeypatch.delenv("SPYQQQ_DASHBOARD_PASSWORD_HASH", raising=False)
    assert c.get("/").status_code == 200
    assert c.get("/", headers={"X-Forwarded-For": "100.64.0.9"}).status_code == 503
    assert c.get("/api/ledger", environ_base={"REMOTE_ADDR": "192.168.1.5"}).status_code == 503


def test_password_gates_pages_and_api(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("SPYQQQ_DASHBOARD_PASSWORD_HASH", _hash("correct horse battery"))
    assert c.get("/").status_code == 302 and c.get("/").headers["Location"].endswith("/login")
    assert c.get("/api/ledger").status_code == 401
    assert c.get("/health").status_code == 200
    assert c.post("/login", data={"password": "nope"}).status_code == 200          # re-shows form
    assert c.get("/api/ledger").status_code == 401
    r = c.post("/login", data={"password": "correct horse battery"})
    assert r.status_code == 302
    assert c.get("/api/ledger").status_code == 200
    assert c.get("/").headers.get("X-Frame-Options") == "SAMEORIGIN"
    c.post("/logout")
    assert c.get("/api/ledger").status_code == 401


def test_login_locks_out_after_five_failures(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("SPYQQQ_DASHBOARD_PASSWORD_HASH", _hash("correct horse battery"))
    for _ in range(5):
        c.post("/login", data={"password": "x"}, headers={"X-Forwarded-For": "100.64.0.7"})
    r = c.post("/login", data={"password": "correct horse battery"}, headers={"X-Forwarded-For": "100.64.0.7"})
    assert r.status_code == 200 and b"Too many" in r.data


def test_lockout_uses_proxy_appended_ip_and_global_cap(client, monkeypatch):
    c, _ = client
    import spyqqq.web as web
    web._FAILS.clear()
    monkeypatch.setenv("SPYQQQ_DASHBOARD_PASSWORD_HASH", _hash("correct horse battery"))
    for k in range(5):     # attacker rotates the claimed (leftmost) address; the proxy's stays the same
        c.post("/login", data={"password": "x"}, headers={"X-Forwarded-For": f"10.0.0.{k}, 203.0.113.9"})
    r = c.post("/login", data={"password": "correct horse battery"}, headers={"X-Forwarded-For": "1.1.1.1, 203.0.113.9"})
    assert b"Too many" in r.data
    web._FAILS.clear()
    for k in range(20):    # many real addresses → global cap
        c.post("/login", data={"password": "x"}, headers={"X-Forwarded-For": f"198.51.100.{k}"})
    r = c.post("/login", data={"password": "correct horse battery"}, headers={"X-Forwarded-For": "192.0.2.1"})
    assert b"Too many" in r.data
    web._FAILS.clear()
