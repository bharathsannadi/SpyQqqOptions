"""cli.py — `spyqqq <command>`.

run [--dry]   start the engine (what launchd runs); one per STATE_DIR — to dry-run beside
              the live engine set SPYQQQ_STATE_DIR to a scratch dir
status        one-screen state from state/status.json
positions     open spreads
halt / resume touch / remove state/HALT (no new entries; exits keep running)
flatten       ask the running engine to close everything now (via HALT+FLATTEN file)
report [date] print (and regenerate) a daily report
history       the historical confidence table
web           start the read-only dashboard on :5050
set-password  set the dashboard login password (stored hashed in .env)
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


class _ETFormatter(logging.Formatter):
    """Log timestamps in US/Eastern with the offset — the ledger, reports and dashboard
    are all ET, and a host-local (CST) clock made cross-referencing incidents error-prone."""
    def formatTime(self, record, datefmt=None):
        from datetime import datetime
        import pytz
        return datetime.fromtimestamp(record.created, pytz.timezone("America/New_York")).strftime("%Y-%m-%d %H:%M:%S%z")


def _logging(name: str) -> None:
    # pid on every line: two processes writing one log (an overlapping restart, a manual
    # `run --dry`) were indistinguishable before.
    fmt = _ETFormatter("%(asctime)s %(levelname)-7s pid=%(process)d %(name)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    if sys.stdout.isatty():                # under launchd stdout is a file → it would duplicate every line, unrotated
        sh = logging.StreamHandler(sys.stdout); sh.setFormatter(fmt); root.addHandler(sh)
    fh = RotatingFileHandler(config.LOG_DIR / f"{name}.log", maxBytes=5_000_000, backupCount=5)
    fh.setFormatter(fmt); root.addHandler(fh)
    for noisy in ("httpx", "urllib3", "werkzeug"):   # werkzeug = one line per dashboard poll
        logging.getLogger(noisy).setLevel(logging.WARNING)


_LOCK_FH = None


def _single_instance(name: str) -> None:
    """Exit if another `spyqqq run` holds the lock. Two engines on one account can each
    pass the open-position caps and double the exposure. flock is released by the OS when
    the process dies, so a crash never leaves a stale lock."""
    import fcntl, os
    global _LOCK_FH
    path = config.STATE_DIR / f"{name}.lock"
    _LOCK_FH = open(path, "a+")
    try:
        fcntl.flock(_LOCK_FH, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _LOCK_FH.seek(0)
        other = _LOCK_FH.read().strip() or "?"
        logging.getLogger("spyqqq.cli").error(f"[cli] another {name} is running (pid {other}) — exiting")
        sys.exit(3)
    _LOCK_FH.seek(0); _LOCK_FH.truncate(); _LOCK_FH.write(str(os.getpid())); _LOCK_FH.flush()


def _keep_awake() -> None:
    """Hold an idle-sleep assertion for exactly as long as this process lives (caffeinate -w).
    Without it the Mac idle-slept through market hours and the engine only ran during
    ~20-min DarkWakes — missing the open and leaving open spreads unmanaged (2026-11-17)."""
    import os, shutil, subprocess
    if sys.platform != "darwin" or not shutil.which("caffeinate"):
        return
    try:
        subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        logging.getLogger("spyqqq.cli").info("[cli] caffeinate -i -s held for the engine's lifetime")
    except Exception as e:
        logging.getLogger("spyqqq.cli").warning(f"[cli] could not start caffeinate: {e}")


def _status() -> dict:
    if not config.STATUS_PATH.exists():
        return {}
    return json.loads(config.STATUS_PATH.read_text())


def cmd_run(a):
    _logging("engine-dry" if a.dry else "engine")   # a manual dry run must not interleave with the live log
    # ONE lock for live and dry: both write the same heartbeat, status, ledger and day_state
    # (budget / trade counts / open directions) — a dry engine beside the live one would
    # corrupt its risk caps and mask a hung engine from the watchdog. The lock lives in
    # STATE_DIR, so `SPYQQQ_STATE_DIR=/tmp/x spyqqq run --dry` is the way to test alongside.
    _single_instance("engine")
    if not a.dry:
        _keep_awake()
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
    today = DayState().date
    day = a.date or today
    # equity / budget / confidence in status.json are *today's* — stamping them on a past
    # day's report would misstate it; regenerated past reports show "—" for those instead
    live = day == today
    path = report.write(day, Ledger(), account=_status().get("account") if live else None,
                        hit_rates=(_status().get("history") or None) if live else None,
                        day=DayState.load() if live else None)
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
    _logging("web-app")      # app warnings (chart data fetch etc.); launchd's web.log keeps crashes only
    from .web import app
    app.run(host="127.0.0.1", port=a.port, debug=False)


def cmd_set_password(a):
    """Prompted, never echoed, stored only as a salted hash in .env. Restart the web
    agent afterwards (launchctl kickstart -k gui/$UID/com.spyqqq.web)."""
    import getpass, os, re
    from werkzeug.security import generate_password_hash
    pw = getpass.getpass("New dashboard password (min 12 chars): ")
    if len(pw) < 12:
        sys.exit("too short — use at least 12 characters")
    if getpass.getpass("Repeat: ") != pw:
        sys.exit("passwords differ")
    env = config.ROOT / ".env"
    text = env.read_text() if env.exists() else ""
    line = f"SPYQQQ_DASHBOARD_PASSWORD_HASH='{generate_password_hash(pw)}'"
    pat = re.compile(r"^SPYQQQ_DASHBOARD_PASSWORD_HASH=.*$", re.M)
    text = pat.sub(line, text) if pat.search(text) else text.rstrip("\n") + "\n" + line + "\n"
    env.write_text(text); os.chmod(env, 0o600)
    print("saved — now restart the dashboard: launchctl kickstart -k gui/$(id -u)/com.spyqqq.web")


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
    sub.add_parser("set-password").set_defaults(f=cmd_set_password)
    w = sub.add_parser("web"); w.add_argument("--port", type=int, default=5050); w.set_defaults(f=cmd_web)
    a = p.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
