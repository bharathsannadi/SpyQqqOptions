"""signal.py — the one entry rule, as pure arithmetic over bar lists.

Setup "trend_pullback":
  daily   : close vs SMA(20) fixes the only direction we may trade today.
  5-minute: the prior bar closed on the wrong side of EMA(9) (a pullback) and
            this bar closed back through it in the trend direction, with RSI(14)
            in the no-chase band and volume ≥ 0.8× the session average.
Mirrored for bears. Same function drives live scans AND the historical replay
in history.py, so the confidence number measures exactly the rule we trade.

Setup "vwap_momentum" (ported from AlpacaTrader, the one signal that showed real
directional edge on the 3-year Polygon diagnostic: 55–60% hit at 30–60 min,
+0.6 ATR mean move, all six symbols; only the naked-option wrapper lost money):
  close above session VWAP, above VWAP on >50% of the last 30 bars, EMA9 > EMA21,
  MACD(12,26,9) histogram > 0, bar volume ≥ 1.0× the trailing 20-bar average,
  closing up. Mirrored for bears. Trend-agnostic by design (the old bot ran it
  without a daily filter); EMA/MACD are warmed up on the prior session's bars.
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


SETUPS = ("trend_pullback", "vwap_momentum")
TREND_REQUIRED = {"trend_pullback": True, "vwap_momentum": False}


def trend_required(setup: str) -> bool:
    if setup == "vwap_momentum":
        return bool(getattr(config, "VWAP_REQUIRE_TREND", False))
    return TREND_REQUIRED.get(setup, True)


def evaluate(sym: str, trend: Optional[str], bars: Sequence[Bar],
             setup: str = "trend_pullback", warmup: Sequence[Bar] = ()) -> Optional[Signal]:
    """Evaluate one setup on the LAST bar of `bars` (today's closed 5-min bars)."""
    if setup == "vwap_momentum":
        s = evaluate_vwap_momentum(sym, bars, warmup)
        if s is not None and getattr(config, "VWAP_REQUIRE_TREND", False) and s.direction != trend:
            return None                          # only trade momentum WITH the daily trend
        return s
    return evaluate_trend_pullback(sym, trend, bars)


def evaluate_all(sym: str, trend: Optional[str], bars: Sequence[Bar],
                 warmup: Sequence[Bar] = ()) -> list[Signal]:
    out = []
    for st in config.SETUPS_ENABLED:
        s = evaluate(sym, trend, bars, st, warmup)
        if s is not None:
            out.append(s)
    return out


def vwap_series(bars: Sequence[Bar]) -> list[float]:
    cum_pv = cum_v = 0.0
    out = []
    for b in bars:
        tp = (b.high + b.low + b.close) / 3.0
        cum_pv += tp * b.volume; cum_v += b.volume
        out.append(cum_pv / cum_v if cum_v > 0 else b.close)
    return out


def macd_hist(values: Sequence[float], fast: int = 12, slow: int = 26, sig: int = 9) -> Optional[float]:
    if len(values) < slow + sig:
        return None
    ef, es = ema_series(values, fast), ema_series(values, slow)
    macd = [f - s for f, s in zip(ef, es) if f is not None and s is not None]
    if len(macd) < sig:
        return None
    signal_line = ema_series(macd, sig)[-1]
    return None if signal_line is None else macd[-1] - signal_line


def evaluate_vwap_momentum(sym: str, bars: Sequence[Bar], warmup: Sequence[Bar] = (),
                           setup: str = "vwap_momentum") -> Optional[Signal]:
    if len(bars) < 3:
        return None
    series = list(warmup) + list(bars)            # EMAs/MACD/volume need > one session
    closes = [b.close for b in series]
    if len(closes) < 26 + 9 + 1:
        return None
    e9, e21 = ema_series(closes, 9)[-1], ema_series(closes, 21)[-1]
    mh = macd_hist(closes)
    if e9 is None or e21 is None or mh is None:
        return None
    vw = vwap_series(bars)[-1]                     # session VWAP: today only
    recent = series[-30:]
    rv = vwap_series(bars)                         # above-VWAP share over today's bars (≤30)
    above = sum(1 for b, v in zip(bars[-30:], rv[-30:]) if b.close > v) / min(len(bars), 30)
    vols = [b.volume for b in series[-21:-1]]
    vol_ratio = series[-1].volume / (sum(vols) / len(vols)) if vols and sum(vols) > 0 else 0.0
    cur, prev = series[-1], series[-2]
    r = rsi(closes, config.RSI_LEN)
    ind = {"vwap": round(vw, 2), "above_vwap_pct": round(above, 2), "ema9": round(e9, 2),
           "ema21": round(e21, 2), "macd_hist": round(mh, 4), "vol_ratio": round(vol_ratio, 2),
           "rsi14": round(r, 1) if r is not None else None, "close": cur.close}
    closing_up = cur.close > prev.close
    if (cur.close > vw and above > 0.50 and e9 > e21 and mh > 0 and closing_up
            and vol_ratio >= config.VWAP_MIN_VOL_RATIO):
        direction = "bull"
    elif (cur.close < vw and above < 0.50 and e9 < e21 and mh < 0 and not closing_up
            and vol_ratio >= config.VWAP_MIN_VOL_RATIO):
        direction = "bear"
    else:
        return None
    strength = max(0.0, min(1.0, 0.5 + abs(above - 0.5) * 0.6 + min(vol_ratio - 1.0, 1.0) * 0.2))
    return Signal(sym=sym, direction=direction, setup=setup, strength=round(strength, 2),
                  price=cur.close, ts=cur.ts, indicators=ind)


def evaluate_trend_pullback(sym: str, trend: Optional[str], bars: Sequence[Bar],
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
