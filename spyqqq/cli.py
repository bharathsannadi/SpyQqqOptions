"""cli.py — `spyqqq <command>`.

run [--dry]   start the engine (what launchd runs)
status        one-screen state from state/status.json
positions     open spreads
halt / resume touch / remove state/HALT (no new entries; exits keep running)
flatten       ask the running engine to close everything now (via HALT+FLATTEN file)
report [date] print (and regenerate) a daily report
history       the historical confidence table
web           start the read-only dashboard on :5050
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

from . import config

load_dotenv(config.ROOT / ".env")


def _logging(name: str) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout); sh.setFormatter(fmt); root.addHandler(sh)
    fh = RotatingFileHandler(config.LOG_DIR / f"{name}.log", maxBytes=5_000_000, backupCount=5)
    fh.setFormatter(fmt); root.addHandler(fh)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _status() -> dict:
    if not config.STATUS_PATH.exists():
        return {}
    return json.loads(config.STATUS_PATH.read_text())


def cmd_run(a):
    _logging("engine")
    from .engine import Engine
    Engine(dry_run=a.dry).run()


def cmd_status(a):
    s = _status()
    if not s:
        print("no status yet — is the engine running?"); return
    import time
    age = time.time() - s.get("epoch", 0)
    print(f"{s['ts']}  mode={s['mode']}  connected={s['connected']}  market_open={s['market_open']}  "
          f"status_age={age:.0f}s{'  ⚠ STALE' if age > config.HEARTBEAT_STALE_SEC else ''}"
          f"{'  🛑 HALTED' if s.get('halted') else ''}")
    acct = s.get("account") or {}
    d = s["day"]
    print(f"equity ${acct.get('equity', 0):,.2f}  ·  day {d['date']}: trades {d['trades']}/{d['max_trades']}, "
          f"debit ${d['debit_spent_usd']:,.0f}/${d['budget_usd']:,.0f}, realised ${d['realized_usd']:+,.0f}")
    for sym, sig in (s.get("signals") or {}).items():
        sg = sig.get("signal")
        line = f"{sym}: trend={sig.get('trend')} price={sig.get('price')} ind={sig.get('indicators')}"
        if sg:
            line += f"  SIGNAL {sg['direction']} strength {sg['strength']}  accepted={sig.get('accepted')}"
        print(line)
        for g in sig.get("gates", []):
            print(f"    {'✓' if g['ok'] else '✗'} {g['text']}")
    for p in s.get("open", []):
        print(f"OPEN {p['sym']} {p['direction']} {p['long']}/{p['short']} x{p['qty']} "
              f"entry {p['entry']} mid {p['mid']} P&L ${p['pnl_usd']:+,.0f} ({p['pnl_pct']:+.1f}%) stop {p['stop']}")


def cmd_positions(a):
    s = _status()
    print(json.dumps(s.get("open", []), indent=2))


def cmd_halt(a):
    config.HALT_PATH.write_text("halted by operator")
    print("HALT set — no new entries; open positions keep being managed")


def cmd_resume(a):
    if config.HALT_PATH.exists():
        config.HALT_PATH.unlink()
    print("HALT cleared")


def cmd_report(a):
    _logging("report")
    from .ledger import Ledger
    from . import report
    from .risk import DayState
    day = a.date or DayState().date
    path = report.write(day, Ledger(), account=_status().get("account"),
                        hit_rates=_status().get("history") or None, day=DayState.load())
    print(Path(path).read_text())


def cmd_history(a):
    s = _status()
    for sym, hrs in (s.get("history") or {}).items():
        for st, by_dir in hrs.items():
            for d, h in by_dir.items():
                print(f"{sym} {st:15s} {d:4s}: {h['label']}")
    if not s.get("history"):
        print("no history yet — the engine computes it at boot")


def cmd_web(a):
    from .web import app
    app.run(host="127.0.0.1", port=a.port, debug=False)


def main(argv=None):
    p = argparse.ArgumentParser(prog="spyqqq")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--dry", action="store_true"); r.set_defaults(f=cmd_run)
    sub.add_parser("status").set_defaults(f=cmd_status)
    sub.add_parser("positions").set_defaults(f=cmd_positions)
    sub.add_parser("halt").set_defaults(f=cmd_halt)
    sub.add_parser("resume").set_defaults(f=cmd_resume)
    rp = sub.add_parser("report"); rp.add_argument("date", nargs="?"); rp.set_defaults(f=cmd_report)
    sub.add_parser("history").set_defaults(f=cmd_history)
    w = sub.add_parser("web"); w.add_argument("--port", type=int, default=5050); w.set_defaults(f=cmd_web)
    a = p.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
