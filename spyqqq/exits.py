"""exits.py — when does an open spread come off?

Wraps the carried ExitEngine (monotonic profit floor) with the three rules the
operator set for this bot: ±20% on net debit, a 90-minute stall, and a hard
flatten at 15:45 ET. Pure: the engine calls `check()` each tick with the
current net mid; it never places orders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional

from . import config
from .exit_engine import ExitConfig, ExitEngine, ExitState


@dataclass
class OpenSpread:
    pos_id: str
    sym: str
    direction: str            # bull | bear
    setup: str
    long_occ: str
    short_occ: str
    qty: int
    net_debit: float          # per contract, what we paid
    opened_at: datetime       # tz-aware ET
    exit: ExitState
    peak_pct: float = 0.0
    last_mid: float = field(default=0.0)

    def pnl_pct(self, mid: float) -> float:
        return (mid - self.net_debit) / self.net_debit if self.net_debit else 0.0

    def pnl_usd(self, mid: float) -> float:
        return (mid - self.net_debit) * 100.0 * self.qty


def new_engine() -> ExitEngine:
    return ExitEngine(ExitConfig(be_trigger=config.EXIT_BE_TRIGGER, trail=config.EXIT_TRAIL,
                                 floor2_gain=config.EXIT_FLOOR2_GAIN,
                                 floor2_lock=config.EXIT_FLOOR2_LOCK,
                                 init_stop_frac=config.SL_PCT))


def check(pos: OpenSpread, mid: float, now: datetime,
          engine: Optional[ExitEngine] = None) -> tuple[str, str]:
    """Return (action, reason). action ∈ {"hold", "close"}.

    Order of checks mirrors the KB: hard time rules first, then the profit
    target, then the ratcheting stop (which starts at −SL_PCT)."""
    engine = engine or new_engine()
    pct = pos.pnl_pct(mid)
    pos.peak_pct = max(pos.peak_pct, pct)
    pos.last_mid = mid

    if now.timetz().replace(tzinfo=None) >= config.FLATTEN_AT_ET:
        return "close", f"EOD flatten {config.FLATTEN_AT_ET:%H:%M} ET ({pct:+.1%})"
    if pct >= config.TP_PCT:
        return "close", f"target +{config.TP_PCT:.0%} hit ({pct:+.1%})"
    held_min = (now - pos.opened_at).total_seconds() / 60.0
    if held_min >= config.STALL_MINUTES and 0.0 <= pct < config.EXIT_BE_TRIGGER:
        return "close", (f"stall: {held_min:.0f} min held, only {pct:+.1%} "
                         f"(< +{config.EXIT_BE_TRIGGER:.0%}) — theta is the enemy (KB §3)")
    action, why, _ = engine.update(pos.exit, high=mid, low=mid, last=mid)
    if action == "exit":
        return "close", f"{why} ({pct:+.1%})"
    return "hold", why


def is_flatten_time(now: datetime) -> bool:
    return now.timetz().replace(tzinfo=None) >= config.FLATTEN_AT_ET
