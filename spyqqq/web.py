"""web.py — read-only dashboard. Reads state/status.json, the ledger and the
reports directory; never imports the engine. If this page dies the bot doesn't."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

from flask import Flask, jsonify, render_template, request

from . import config, report
from .ledger import Ledger

app = Flask(__name__, template_folder=str(config.ROOT / "web" / "templates"),
            static_folder=str(config.ROOT / "web" / "static"))


def _asset_version() -> int:
    """mtime of the newest static asset — busts the browser cache on every deploy."""
    static = config.ROOT / "web" / "static"
    return int(max((p.stat().st_mtime for p in static.glob("*")), default=0))


@app.get("/")
def index():
    return render_template("index.html", v=_asset_version())


@app.get("/api/status")
def api_status():
    if not config.STATUS_PATH.exists():
        return jsonify({"error": "no status yet"}), 404
    return app.response_class(config.STATUS_PATH.read_text(), mimetype="application/json")


@app.get("/api/ledger")
def api_ledger():
    day = request.args.get("day") or datetime.now().strftime("%Y-%m-%d")
    rows = list(Ledger().rows(day))
    return jsonify(rows[-200:])


@app.get("/api/reports")
def api_reports():
    return jsonify(sorted(p.stem for p in config.REPORT_DIR.glob("*.md"))[::-1])


@app.get("/api/report/<day>")
def api_report(day):
    p = config.REPORT_DIR / f"{day}.md"
    if not p.exists():
        return jsonify({"error": "no report"}), 404
    return app.response_class(p.read_text(), mimetype="text/markdown")


@app.get("/api/summary")
def api_summary():
    """Closed-trade history for the performance panel: cumulative P&L, per-day net,
    rolling 20-session stats, and the list of days that have any ledger rows."""
    led = Ledger()
    by_id: dict = {}
    days: set = set()
    for r in led.rows():
        days.add(r["ts"][:10])
        if r["kind"] == "close" and r.get("exit") is not None:
            by_id[r.get("pos_id") or r["ts"]] = r
    closes = sorted(by_id.values(), key=lambda r: r["ts"])
    cum = 0.0
    series = []
    by_day: dict = defaultdict(lambda: {"net": 0.0, "n": 0})
    for r in closes:
        pnl = float(r.get("pnl_usd") or 0)
        cum += pnl
        series.append({"ts": r["ts"], "sym": r["sym"], "setup": r.get("setup"),
                       "direction": r.get("direction"), "pnl_usd": round(pnl, 2),
                       "pnl_pct": r.get("pnl_pct"), "reason": r.get("reason"), "cum": round(cum, 2)})
        by_day[r["ts"][:10]]["net"] += pnl
        by_day[r["ts"][:10]]["n"] += 1
    return jsonify({"rolling": report.rolling(led), "series": series,
                    "by_day": [{"day": d, "net": round(v["net"], 2), "n": v["n"]} for d, v in sorted(by_day.items())],
                    "days": sorted(days, reverse=True)})


@app.get("/api/config")
def api_config():
    keys = [k for k in dir(config) if k.isupper() and not k.endswith(("_PATH", "_DIR"))]
    return app.response_class(json.dumps({k: getattr(config, k) for k in keys}, default=str),
                              mimetype="application/json")
