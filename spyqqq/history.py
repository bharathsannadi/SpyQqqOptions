"""history.py — "confidence based on historic data", measured not scored.

Replays signal.evaluate() over cached 5-minute bars, day by day, exactly as the
live scanner would have seen them, and records what the underlying did next:
first touch of +HIST_TARGET_PCT vs −HIST_STOP_PCT before the flatten time,
else the sign of the move at flatten. Confidence is the Wilson 95% LOWER bound
of the hit rate — a small sample can't fake a high number.

Everything is pure over bar lists; data.py supplies the bars.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, time
from typing import Optional, Sequence

from . import config, signal as sig

HIST_TARGET_PCT = 0.0030    # +0.30% on the underlying ≈ +20% on an ATM debit spread
HIST_STOP_PCT   = 0.0030


@dataclass
class HitRate:
    sym: str
    direction: str
    n: int
    hits: int
    rate: Optional[float]
    lower95: Optional[float]
    upper95: Optional[float]
    avg_move_pct: Optional[float]

    @property
    def confidence(self) -> Optional[float]:
        return self.lower95

    def as_dict(self) -> dict:
        return {"sym": self.sym, "direction": self.direction, "n": self.n, "hits": self.hits,
                "rate": None if self.rate is None else round(self.rate, 3),
                "lower95": None if self.lower95 is None else round(self.lower95, 3),
                "upper95": None if self.upper95 is None else round(self.upper95, 3),
                "avg_move_pct": None if self.avg_move_pct is None else round(self.avg_move_pct, 3),
                "label": self.label()}

    def label(self) -> str:
        if self.rate is None:
            return f"no history (n={self.n})"
        return (f"{self.rate:.0%} hit rate, n={self.n}, "
                f"95% CI {self.lower95:.0%}–{self.upper95:.0%}")


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[Optional[float], Optional[float]]:
    if n <= 0:
        return None, None
    p = hits / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - half) / denom, (centre + half) / denom


def _in_window(t: time) -> bool:
    return config.ENTRY_WINDOW_START_ET <= t <= config.ENTRY_WINDOW_END_ET


def outcome(direction: str, entry: float, later: Sequence[sig.Bar]) -> tuple[bool, float]:
    """(hit, move_pct_at_resolution). First-touch on bar highs/lows, else sign at flatten."""
    sgn = 1 if direction == "bull" else -1
    tgt = entry * (1 + sgn * HIST_TARGET_PCT)
    stp = entry * (1 - sgn * HIST_STOP_PCT)
    last = entry
    for b in later:
        if b.ts.time() > config.FLATTEN_AT_ET:
            break
        last = b.close
        hi_hit = b.high >= tgt if sgn > 0 else b.low <= tgt
        lo_hit = b.low <= stp if sgn > 0 else b.high >= stp
        if hi_hit and lo_hit:          # both in one bar: assume the stop (conservative)
            return False, -HIST_STOP_PCT
        if hi_hit:
            return True, HIST_TARGET_PCT
        if lo_hit:
            return False, -HIST_STOP_PCT
    mv = (last - entry) / entry * sgn
    return mv > 0, mv


def replay(sym: str, daily_closes_by_date: dict[date, Sequence[float]],
           intraday: dict[date, Sequence[sig.Bar]]) -> dict[str, HitRate]:
    """`daily_closes_by_date[d]` = closes up to and including d-1 (what was known
    at the open of d). `intraday[d]` = that day's 5-min bars in order."""
    stats: dict[str, list[tuple[bool, float]]] = defaultdict(list)
    for d in sorted(intraday):
        bars = list(intraday[d])
        trend = sig.daily_trend(daily_closes_by_date.get(d, []))
        if trend is None:
            continue
        fired = False
        for i in range(2, len(bars)):
            if fired:
                break
            if not _in_window(bars[i].ts.time()):
                continue
            s = sig.evaluate(sym, trend, bars[:i + 1])
            if s is None:
                continue
            hit, mv = outcome(s.direction, s.price, bars[i + 1:])
            stats[s.direction].append((hit, mv))
            fired = True                 # one trade per day, like the live engine
    out: dict[str, HitRate] = {}
    for direction in ("bull", "bear"):
        rows = stats.get(direction, [])
        n = len(rows); hits = sum(1 for h, _ in rows if h)
        lo, hi = wilson(hits, n)
        out[direction] = HitRate(sym, direction, n, hits,
                                 (hits / n) if n else None, lo, hi,
                                 (sum(m for _, m in rows) / n * 100) if n else None)
    return out


def confidence_ok(hr: Optional[HitRate]) -> tuple[bool, str]:
    if hr is None or hr.n < config.MIN_HISTORY_N or hr.lower95 is None:
        n = hr.n if hr else 0
        return False, (f"only {n} historical occurrences (< {config.MIN_HISTORY_N}) — "
                       f"can't rate this setup yet (KB §12 validation discipline)")
    if hr.lower95 < config.MIN_CONFIDENCE:
        return False, (f"historical confidence {hr.lower95:.0%} (Wilson lower, n={hr.n}) "
                       f"< {config.MIN_CONFIDENCE:.0%} floor — edge not demonstrated (KB §12)")
    return True, (f"historical confidence {hr.lower95:.0%} (hit rate {hr.rate:.0%}, n={hr.n}) "
                  f"≥ {config.MIN_CONFIDENCE:.0%} (KB §12)")
