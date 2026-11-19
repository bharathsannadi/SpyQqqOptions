"""web.py — read-only dashboard. Reads state/status.json, the ledger and the
reports directory; never imports the engine. If this page dies the bot doesn't."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

import os
import secrets
import time
from datetime import timedelta

from flask import Flask, jsonify, redirect, render_template, request, session

from . import config, report
from .ledger import Ledger

app = Flask(__name__, template_folder=str(config.ROOT / "web" / "templates"),
            static_folder=str(config.ROOT / "web" / "static"))
# a stable key keeps logins across restarts; without one, logins just reset on restart
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                  PERMANENT_SESSION_LIFETIME=timedelta(days=30))


# ── auth ─────────────────────────────────────────────────────────────────────
# The dashboard shows account equity, the broker key prefix and every decision. It is
# reached remotely only through Tailscale Serve (Flask stays on 127.0.0.1), and a
# password is required on top of that. No password set → remote requests are refused.
_FAILS: dict[str, list[float]] = {}
_OPEN = {"login", "logout", "health"}


def _pw_hash() -> str:
    return os.environ.get("SPYQQQ_DASHBOARD_PASSWORD_HASH", "").strip()


def _client_ip() -> str:
    return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr or "?")


def _is_proxied() -> bool:
    """Tailscale Serve (and any reverse proxy) forwards from 127.0.0.1 but adds these."""
    return bool(request.headers.get("X-Forwarded-For") or request.headers.get("Tailscale-User-Login")
                or request.remote_addr not in ("127.0.0.1", "::1"))


def _locked_out(ip: str) -> bool:
    now = time.time()
    _FAILS[ip] = [t for t in _FAILS.get(ip, []) if now - t < 900]
    return len(_FAILS[ip]) >= 5


@app.before_request
def _require_login():
    if request.endpoint in _OPEN:
        return None
    if not _pw_hash():
        if _is_proxied():
            return ("Dashboard password not set — run `spyqqq set-password` on the Mac.", 503)
        return None                                  # local-only use without a password keeps working
    if session.get("auth"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "login required"}), 401
    return redirect("/login")


@app.after_request
def _headers(resp):
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/api/") or request.endpoint == "index":
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/login", methods=["GET", "POST"])
def login():
    from werkzeug.security import check_password_hash
    err = ""
    if request.method == "POST":
        ip = _client_ip()
        if _locked_out(ip):
            err = "Too many attempts — wait 15 minutes."
        elif _pw_hash() and check_password_hash(_pw_hash(), request.form.get("password", "")):
            session.clear(); session.permanent = True; session["auth"] = True
            _FAILS.pop(ip, None)
            return redirect("/")
        else:
            _FAILS.setdefault(ip, []).append(time.time())
            err = "Wrong password."
            app.logger.warning(f"[web] failed dashboard login from {ip}")
    return render_template("login.html", err=err, configured=bool(_pw_hash()))


@app.post("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.get("/health")
def health():
    return {"ok": True}


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
    # sort by timestamp: rows are appended in write order, which can differ from event
    # order when two processes overlap (e.g. a restart racing the old engine's shutdown)
    rows = sorted(Ledger().rows(day), key=lambda r: r.get("ts", ""))
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


@app.get("/api/chart/<sym>")
def api_chart(sym):
    from . import chart
    try:
        return jsonify(chart.build(sym, request.args.get("day")))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.get("/api/chart/<sym>/days")
def api_chart_days(sym):
    from . import chart
    return jsonify(chart.sessions(sym.upper())[:250])


@app.get("/api/history_extra")
def api_history_extra():
    from . import chart
    return jsonify(chart.history_extra())


@app.get("/api/why/<sym>")
def api_why(sym):
    from . import chart
    try:
        return jsonify(chart.diagnose(sym))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
