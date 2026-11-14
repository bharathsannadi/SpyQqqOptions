"""ledger.py — append-only JSONL record of every decision, fill and close.

Design rules (each one fixes a defect found in the old ledger on 2026-11-14):
  * `setup` and `mode` are REQUIRED on every row — 59% of old rows had no setup.
  * rejections are rows too, with the failing KB § sentence — "why zero trades"
    is answerable from the ledger alone.
  * fills carry expected mid AND fill price so slippage is measured, not guessed.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

import pytz

from . import config

ET = pytz.timezone("America/New_York")

KINDS = ("decision", "fill", "close", "orphan", "engine")
REQUIRED = ("ts", "kind", "mode", "setup", "sym")


def _now() -> str:
    return datetime.now(ET).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, path: Path = config.LEDGER_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ── write ────────────────────────────────────────────────────────────────
    def write(self, row: dict) -> dict:
        row = {"ts": _now(), "mode": "dry" if config.DRY_RUN else "paper", **row}
        if row.get("kind") not in KINDS:
            raise ValueError(f"ledger kind must be one of {KINDS}, got {row.get('kind')!r}")
        missing = [k for k in REQUIRED if not row.get(k)]
        if missing:
            raise ValueError(f"ledger row missing required fields {missing}: {row}")
        with self.path.open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")
        return row

    def decision(self, sym: str, setup: str, direction: str, accepted: bool,
                 gates: list[dict], confidence: Optional[dict] = None,
                 debate: Optional[dict] = None, **extra) -> dict:
        return self.write({"kind": "decision", "sym": sym, "setup": setup,
                           "direction": direction, "accepted": accepted,
                           "gates": gates, "confidence": confidence, "debate": debate,
                           **extra})

    def fill(self, sym: str, setup: str, **extra) -> dict:
        return self.write({"kind": "fill", "sym": sym, "setup": setup, **extra})

    def close(self, sym: str, setup: str, **extra) -> dict:
        return self.write({"kind": "close", "sym": sym, "setup": setup, **extra})

    def engine(self, event: str, **extra) -> dict:
        return self.write({"kind": "engine", "sym": "-", "setup": "engine",
                           "event": event, **extra})

    # ── read ─────────────────────────────────────────────────────────────────
    def rows(self, day: Optional[str] = None) -> Iterator[dict]:
        if not self.path.exists():
            return
        with self.path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if day is None or row.get("ts", "")[:10] == day:
                    yield row

    def today(self) -> list[dict]:
        return list(self.rows(datetime.now(ET).strftime("%Y-%m-%d")))

    def open_positions(self) -> dict[str, dict]:
        """Fills without a matching close, keyed by position id."""
        opened: dict[str, dict] = {}
        for r in self.rows():
            if r["kind"] == "fill":
                opened[r["pos_id"]] = r
            elif r["kind"] in ("close", "orphan") and r.get("pos_id") in opened:
                del opened[r["pos_id"]]
        return opened
