"""risk.py — the day's money rules, persisted so a restart can't reset them.

Trimmed from AlpacaTrader's RiskBrain: one route (options), one day. The
engine asks `can_enter()` before every order and the answer always carries the
KB sentence that said no.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import pytz

from . import config

ET = pytz.timezone("America/New_York")


def today_str() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def recent_slippage_bps(ledger, n: int) -> list[float]:
    """Last n real fills' slippage in bps of the modelled mid (dry fills excluded)."""
    out = []
    for r in ledger.rows():
        if r["kind"] == "fill" and not r.get("dry") and r.get("expected_mid") and r.get("slippage") is not None:
            out.append(float(r["slippage"]) / float(r["expected_mid"]) * 10_000)
    return out[-n:]


def recent_stops(ledger, sym: str, direction: str, sessions: int) -> int:
    """Stop-outs (closes with a loss, not EOD flattens) for sym+direction over the last `sessions` trading days."""
    days = sorted({r["ts"][:10] for r in ledger.rows() if r["kind"] == "close"})[-sessions:]
    if not days:
        return 0
    return sum(1 for r in ledger.rows()
               if r["kind"] == "close" and r["ts"][:10] in days and r.get("sym") == sym
               and r.get("direction") == direction and (r.get("pnl_usd") or 0) < 0
               and "flatten" not in (r.get("reason") or ""))


def recent_net_usd(ledger, sessions: int) -> float:
    """Net realised P&L (after fees) over the last `sessions` trading days with a close, one row per pos_id."""
    by_id: dict = {}
    for r in ledger.rows():
        if r["kind"] == "close" and r.get("exit") is not None:
            by_id[r.get("pos_id") or r["ts"]] = r
    days = sorted({r["ts"][:10] for r in by_id.values()})[-sessions:]
    return sum(float(r.get("pnl_usd") or 0) for r in by_id.values() if r["ts"][:10] in days)


@dataclass
class DayState:
    date: str = field(default_factory=today_str)
    trades: int = 0
    debit_spent_usd: float = 0.0
    realized_usd: float = 0.0
    open_by_sym: dict = field(default_factory=dict)     # sym -> pos_id
    open_dir_by_sym: dict = field(default_factory=dict) # sym -> bull|bear
    halted_reason: str = ""

    # ── persistence ──────────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: Path = config.DAY_STATE_PATH) -> "DayState":
        if path.exists():
            try:
                d = json.loads(path.read_text())
                st = cls(**d)
                if st.date == today_str():
                    return st
            except Exception:
                pass
        return cls()

    def save(self, path: Path = config.DAY_STATE_PATH) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        tmp.replace(path)

    # ── the gate ─────────────────────────────────────────────────────────────
    def can_enter(self, sym: str, debit_usd: float) -> tuple[bool, str]:
        if config.HALT_PATH.exists():
            return False, "operator HALT file present — no new entries"
        if self.halted_reason:
            return False, self.halted_reason
        if self.realized_usd <= -config.DAILY_LOSS_HALT_USD:
            return False, (f"daily loss halt: realised ${self.realized_usd:,.0f} ≤ "
                           f"−${config.DAILY_LOSS_HALT_USD:,.0f} (KB §4 — stop trading, review)")
        if self.trades >= config.MAX_TRADES_PER_DAY:
            return False, f"max {config.MAX_TRADES_PER_DAY} trades/day reached (KB §17 discipline)"
        if len(self.open_by_sym) >= config.MAX_OPEN:
            return False, f"max {config.MAX_OPEN} open positions (KB §4)"
        if sym in self.open_by_sym:
            return False, f"already have an open {sym} position"
        if debit_usd > config.PER_TRADE_MAX_USD:
            return False, (f"KB §4 Position Sizing: trade risk ${debit_usd:,.0f} > "
                           f"${config.PER_TRADE_MAX_USD:,.0f} per-trade cap")
        if self.debit_spent_usd + debit_usd > config.DAILY_BUDGET_USD:
            return False, (f"daily budget: ${self.debit_spent_usd:,.0f} spent + ${debit_usd:,.0f} > "
                           f"${config.DAILY_BUDGET_USD:,.0f} (KB §4)")
        return True, (f"KB §4 Position Sizing: ${debit_usd:,.0f} ≤ ${config.PER_TRADE_MAX_USD:,.0f}/trade, "
                      f"${self.debit_spent_usd + debit_usd:,.0f} ≤ ${config.DAILY_BUDGET_USD:,.0f}/day")

    def register_entry(self, sym: str, pos_id: str, debit_usd: float, direction: str = "") -> None:
        self.trades += 1
        self.debit_spent_usd += debit_usd
        self.open_by_sym[sym] = pos_id
        if direction:
            self.open_dir_by_sym[sym] = direction
        self.save()

    def register_close(self, sym: str, pnl_usd: float) -> None:
        self.realized_usd += pnl_usd
        self.open_by_sym.pop(sym, None)
        self.open_dir_by_sym.pop(sym, None)
        self.save()

    def open_directions(self) -> list[str]:
        return list(self.open_dir_by_sym.values())

    def contracts_for(self, net_debit: float) -> int:
        """How many spreads fit under the per-trade cap AND what's left of the day."""
        if net_debit <= 0:
            return 0
        per = net_debit * 100.0
        room = min(config.PER_TRADE_MAX_USD, config.DAILY_BUDGET_USD - self.debit_spent_usd)
        return max(0, int(room // per))
