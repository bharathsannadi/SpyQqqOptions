"""web.py — read-only dashboard. Reads state/status.json, the ledger and the
reports directory; never imports the engine. If this page dies the bot doesn't."""
from __future__ import annotations

import json
from datetime import datetime

from flask import Flask, jsonify, render_template, request

from . import config
from .ledger import Ledger

app = Flask(__name__, template_folder=str(config.ROOT / "web" / "templates"),
            static_folder=str(config.ROOT / "web" / "static"))


@app.get("/")
def index():
    return render_template("index.html")


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


@app.get("/api/config")
def api_config():
    keys = [k for k in dir(config) if k.isupper() and not k.endswith(("_PATH", "_DIR"))]
    return jsonify({k: getattr(config, k) for k in keys}, default=str) if False else \
        app.response_class(json.dumps({k: getattr(config, k) for k in keys}, default=str),
                           mimetype="application/json")
