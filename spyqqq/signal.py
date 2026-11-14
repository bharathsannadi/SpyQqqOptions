"""signal.py — the one entry rule, as pure arithmetic over bar lists.

Setup "trend_pullback":
  daily   : close vs SMA(20) fixes the only direction we may trade today.
  5-minute: the prior bar closed on the wrong side of EMA(9) (a pullback) and
            this bar closed back through it in the trend direction, with RSI(14)
            in the no-chase band and volume ≥ 0.8× the session average.
Mirrored for bears. Same function drives live scans AND the historical replay
in history.py, so the confidence number measures exactly the rule we trade.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Sequence

from . import config


@dataclass
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Signal:
    sym: str
    direction: str                     # bull | bear
    setup: str
    strength: float                    # 0..1
    price: float
    ts: datetime
    indicators: dict = field(default_factory=dict)
    # filled in later by the engine
    hv5: Optional[float] = None
    hv30: Optional[float] = None
    iv30: Optional[float] = None
    ivr: Optional[float] = None

    def __post_init__(self):
        if self.direction not in ("bull", "bear"):
            raise ValueError(f"direction must be bull|bear, got {self.direction!r}")


# ── indicators ───────────────────────────────────────────────────────────────

def sma(values: Sequence[float], n: int) -> Optional[float]:
    if len(values) < n or n <= 0:
        return None
    return sum(values[-n:]) / n


def ema_series(values: Sequence[float], n: int) -> list[Optional[float]]:
    out: list[Optional[float]] = []
    k = 2.0 / (n + 1)
    e: Optional[float] = None
    for i, v in enumerate(values):
        if i < n - 1:
            out.append(None); continue
        if e is None:
            e = sum(values[i - n + 1:i + 1]) / n
        else:
            e = v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(values: Sequence[float], n: int = 14) -> Optional[float]:
    if len(values) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(len(values) - n, len(values)):
        d = values[i] - values[i - 1]
        if d >= 0: gains += d
        else:      losses -= d
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100.0 - 100.0 / (1.0 + rs)


# ── the rule ─────────────────────────────────────────────────────────────────

def daily_trend(daily_closes: Sequence[float]) -> Optional[str]:
    """'bull' | 'bear' | None (no trend / not enough data)."""
    m = sma(daily_closes, config.TREND_MA_DAYS)
    if m is None:
        return None
    last = daily_closes[-1]
    if last > m * 1.001: return "bull"
    if last < m * 0.999: return "bear"
    return None


def evaluate(sym: str, trend: Optional[str], bars: Sequence[Bar],
             setup: str = "trend_pullback") -> Optional[Signal]:
    """Evaluate the rule on the LAST bar of `bars` (today's 5-min bars so far)."""
    if trend is None or len(bars) < max(config.PULLBACK_EMA, config.RSI_LEN) + 2:
        return None
    closes = [b.close for b in bars]
    ema = ema_series(closes, config.PULLBACK_EMA)
    e_prev, e_now = ema[-2], ema[-1]
    if e_prev is None or e_now is None:
        return None
    r = rsi(closes, config.RSI_LEN)
    if r is None:
        return None
    cur, prev = bars[-1], bars[-2]
    vols = [b.volume for b in bars[-7:-1]]            # the prior 30 minutes, not the open
    avg_vol = sum(vols) / len(vols) if vols else 0.0
    vol_ratio = cur.volume / avg_vol if avg_vol > 0 else 0.0

    ind = {"ema9": round(e_now, 2), "rsi14": round(r, 1), "vol_ratio": round(vol_ratio, 2),
           "close": cur.close, "prev_close": prev.close, "trend": trend}

    if trend == "bull":
        pullback = prev.close < e_prev
        reclaim  = cur.close > e_now and cur.close > cur.open
        rsi_ok   = config.RSI_MIN <= r <= config.RSI_MAX
    else:
        pullback = prev.close > e_prev
        reclaim  = cur.close < e_now and cur.close < cur.open
        rsi_ok   = (100 - config.RSI_MAX) <= r <= (100 - config.RSI_MIN)
    vol_ok = vol_ratio >= config.VOL_RATIO_MIN
    if not (pullback and reclaim and rsi_ok and vol_ok):
        return None

    # strength: how decisively it reclaimed, scaled by volume (capped)
    reclaim_pct = abs(cur.close - e_now) / e_now
    strength = max(0.0, min(1.0, 0.5 + reclaim_pct * 100 + min(vol_ratio - 0.8, 1.0) * 0.2))
    return Signal(sym=sym, direction=trend, setup=setup, strength=round(strength, 2),
                  price=cur.close, ts=cur.ts, indicators=ind)
