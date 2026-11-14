"""The entry rule and the historical-confidence replay, on synthetic bars."""
from datetime import datetime, timedelta, date
import pytz

from spyqqq import config, history, signal as sig

ET = pytz.timezone("America/New_York")


def _bars(day: date, closes, start="09:30", vol=1000.0):
    t0 = ET.localize(datetime.combine(day, datetime.strptime(start, "%H:%M").time()))
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        out.append(sig.Bar(ts=t0 + timedelta(minutes=5 * i), open=o, high=max(o, c) + 0.05,
                           low=min(o, c) - 0.05, close=c, volume=vol))
    return out


def test_daily_trend_needs_sma_and_direction():
    assert sig.daily_trend([100.0] * 10) is None                       # too short
    assert sig.daily_trend([100.0] * 19 + [103.0]) == "bull"
    assert sig.daily_trend([100.0] * 19 + [97.0]) == "bear"
    assert sig.daily_trend([100.0] * 20) is None                       # flat = no trade


def test_rsi_and_ema_basic():
    up = [100 + i for i in range(20)]
    assert sig.rsi(up, 14) == 100.0
    e = sig.ema_series(up, 9)
    assert e[7] is None and e[8] is not None and e[-1] < up[-1]


def test_pullback_reclaim_fires_bull_only_in_trend():
    # 20 bars drifting up, a dip below EMA9, then a reclaim bar
    closes = [100 + 0.1 * i for i in range(20)] + [101.5, 101.0, 100.7, 101.6]
    bars = _bars(date(2026, 11, 13), closes, start="09:30")
    assert sig.evaluate("SPY", "bull", bars) is not None
    assert sig.evaluate("SPY", "bear", bars) is None
    assert sig.evaluate("SPY", None, bars) is None


def test_no_signal_without_volume():
    closes = [100 + 0.1 * i for i in range(20)] + [101.5, 101.0, 100.7, 101.6]
    bars = _bars(date(2026, 11, 13), closes)
    bars[-1].volume = 10.0           # dead bar
    assert sig.evaluate("SPY", "bull", bars) is None


def test_wilson_lower_bound_penalises_small_samples():
    lo_small, _ = history.wilson(8, 10)
    lo_big, _ = history.wilson(80, 100)
    assert lo_small < lo_big < 0.8


def test_outcome_first_touch_and_flatten_sign():
    d = date(2026, 11, 13)
    entry = 100.0
    later = _bars(d, [100.1, 100.5, 100.0], start="10:30")
    hit, mv = history.outcome("bull", entry, later)     # high 100.55 ≥ +0.30% target
    assert hit and mv == history.HIST_TARGET_PCT
    later = _bars(d, [99.9, 99.6], start="10:30")
    hit, mv = history.outcome("bull", entry, later)
    assert not hit
    later = _bars(d, [100.05, 100.1], start="10:30")    # neither touched → sign at flatten
    hit, mv = history.outcome("bull", entry, later)
    assert hit and 0 < mv < history.HIST_TARGET_PCT


def test_replay_counts_one_trade_per_day_and_rates():
    closes = [100 + 0.1 * i for i in range(20)] + [101.5, 101.0, 100.7, 101.6, 102.2, 102.5]
    days = [date(2026, 11, 3) + timedelta(days=i) for i in range(5)]
    intraday = {d: _bars(d, closes, start="09:30") for d in days}
    daily = {d: [100.0] * 19 + [103.0] for d in days}
    hr = history.replay("SPY", daily, intraday)
    assert hr["bull"].n == 5 and hr["bull"].hits == 5 and hr["bull"].rate == 1.0
    assert hr["bear"].n == 0 and hr["bear"].rate is None


def test_confidence_gate_requires_sample_and_floor(monkeypatch):
    monkeypatch.setattr(config, "MIN_HISTORY_N", 30)
    monkeypatch.setattr(config, "MIN_CONFIDENCE", 0.55)
    small = history.HitRate("SPY", "bull", 10, 9, 0.9, 0.6, 0.98, 0.2)
    ok, why = history.confidence_ok(small)
    assert not ok and "10 historical" in why
    weak = history.HitRate("SPY", "bull", 100, 52, 0.52, 0.42, 0.62, 0.0)
    ok, why = history.confidence_ok(weak)
    assert not ok and "edge not demonstrated" in why
    good = history.HitRate("SPY", "bull", 100, 68, 0.68, 0.58, 0.77, 0.1)
    assert history.confidence_ok(good)[0]


def _trend_bars(day: date, n=90, start_px=100.0, step=0.08, start="09:30"):
    """A clean grinding uptrend with every bar closing up on steady volume."""
    closes = [start_px + step * i for i in range(n)]
    return _bars(day, closes, start=start, vol=1000.0)


def test_vwap_momentum_needs_warmup_and_fires_in_trend():
    d = date(2026, 11, 13)
    warm = _trend_bars(date(2026, 11, 12), n=78, start_px=95.0)
    today = _trend_bars(d, n=10, start_px=101.5)
    today[-1].volume = 1500.0
    assert sig.evaluate_vwap_momentum("SPY", today, warmup=[]) is None          # no MACD without warm-up
    s = sig.evaluate_vwap_momentum("SPY", today, warmup=warm)
    assert s is not None and s.direction == "bull" and s.setup == "vwap_momentum"
    assert s.indicators["macd_hist"] > 0 and s.indicators["above_vwap_pct"] > 0.5


def test_vwap_momentum_bear_mirror_and_no_fire_on_chop():
    d = date(2026, 11, 13)
    warm = _bars(date(2026, 11, 12), [110 - 0.08 * i for i in range(78)])
    today = _bars(d, [103.5 - 0.1 * i for i in range(10)]); today[-1].volume = 1500.0
    s = sig.evaluate_vwap_momentum("SPY", today, warmup=warm)
    assert s is not None and s.direction == "bear"
    chop = _bars(d, [100 + (0.3 if i % 2 else -0.3) for i in range(10)])
    assert sig.evaluate_vwap_momentum("SPY", chop, warmup=warm) is None


def test_evaluate_all_returns_each_enabled_setup(monkeypatch):
    monkeypatch.setattr(config, "SETUPS_ENABLED", ("trend_pullback", "vwap_momentum"))
    d = date(2026, 11, 13)
    warm = _trend_bars(date(2026, 11, 12), n=78, start_px=95.0)
    closes = [100 + 0.1 * i for i in range(20)] + [101.5, 101.0, 100.7, 101.6]
    bars = _bars(d, closes)
    out = sig.evaluate_all("SPY", "bull", bars, warmup=warm)
    assert any(x.setup == "trend_pullback" for x in out)


def test_replay_setup_param_uses_warmup():
    d0, d1 = date(2026, 11, 12), date(2026, 11, 13)
    intraday = {d0: _trend_bars(d0, n=78, start_px=95.0), d1: _trend_bars(d1, n=78, start_px=101.5)}
    for b in intraday[d1]: b.volume = 1200.0
    hr = history.replay("SPY", {d0: [], d1: []}, intraday, setup="vwap_momentum")
    assert hr["bull"].n == 1            # trend-agnostic: fires even with no daily closes
    hr2 = history.replay("SPY", {d0: [], d1: []}, intraday, setup="trend_pullback")
    assert hr2["bull"].n == 0           # trend_pullback needs the daily trend
