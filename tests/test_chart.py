"""Dashboard chart / "why no signal" — the checklist must agree with the real rule.

The checklist in chart.py re-states every condition of signal.py so the operator can
see which one failed. If the two ever drift, the Desk would explain a non-trade with
the wrong reason — so this pins them together bar-by-bar on synthetic sessions.
"""
import json
import random
from datetime import datetime, timedelta

import pytz
import pytest

from spyqqq import chart, config
from spyqqq import signal as sig

ET = pytz.timezone("America/New_York")


def _session(day, seed, drift=0.0, n=78, start=500.0):
    rnd = random.Random(seed)
    t0 = ET.localize(datetime(day.year, day.month, day.day, 9, 30))
    px, out = start, []
    for i in range(n):
        o = px
        c = o * (1 + drift + rnd.gauss(0, 0.0012))
        hi, lo = max(o, c) * (1 + abs(rnd.gauss(0, 0.0004))), min(o, c) * (1 - abs(rnd.gauss(0, 0.0004)))
        out.append(sig.Bar(t0 + timedelta(minutes=5 * i), o, hi, lo, c, rnd.uniform(4e5, 2.5e6)))
        px = c
    return out


def _sessions():
    from datetime import date
    days = [date(2026, 3, 2) + timedelta(days=k) for k in range(8)]
    out = []
    for k, d in enumerate(days):
        out.append(_session(d, seed=k, drift=(0.0004 if k % 2 else -0.0004)))
    return out


@pytest.mark.parametrize("direction", ["bull", "bear"])
def test_vwap_checklist_matches_rule_on_every_bar(direction):
    sess = _sessions()
    fired = agreed = 0
    for warmup, today in zip(sess, sess[1:]):
        for i in range(3, len(today)):
            bars = today[:i + 1]
            s = sig.evaluate_vwap_momentum("SPY", bars, warmup)
            rule = s is not None and s.direction == direction
            checks = chart._vwap_checks(direction, bars, warmup)
            assert all(c["ok"] for c in checks) == rule, (i, [c for c in checks if not c["ok"]])
            fired += rule; agreed += 1
    assert agreed > 400 and fired > 0          # the test actually exercised firing bars


@pytest.mark.parametrize("direction", ["bull", "bear"])
def test_pullback_checklist_matches_rule_on_every_bar(direction):
    fired = 0
    for today in _sessions():
        for i in range(2, len(today)):
            bars = today[:i + 1]
            s = sig.evaluate_trend_pullback("SPY", direction, bars)
            checks = chart._pullback_checks(direction, bars)
            assert all(c["ok"] for c in checks) == (s is not None), (i, [c for c in checks if not c["ok"]])
            fired += s is not None
    assert fired > 0


def test_packed_history_round_trips(tmp_path, monkeypatch):
    sess = _sessions()[:2]
    path = tmp_path / "SPY_5m.jsonl"
    with path.open("w") as f:
        for day in sess:
            for b in day:
                f.write(json.dumps({"ts": b.ts.isoformat(), "open": b.open, "high": b.high, "low": b.low,
                                    "close": b.close, "volume": b.volume}) + "\n")
        f.write(json.dumps({"ts": sess[0][0].ts.isoformat(), "open": 1, "high": 1, "low": 1,
                            "close": 1, "volume": 1}) + "\n")          # duplicate ts: last one wins, like data.history
        f.write("not json\n")
    monkeypatch.setattr(config, "BARS_CACHE_DIR", tmp_path)
    chart._hist.clear()
    h = chart._history("SPY")
    assert sorted(h) == [sess[0][0].ts.date(), sess[1][0].ts.date()]
    got = h[sess[1][0].ts.date()]
    assert [(b.ts, round(b.close, 6)) for b in got] == [(b.ts, round(b.close, 6)) for b in sess[1]]
    assert got[0].ts.tzinfo is not None and got[0].ts.strftime("%H:%M") == "09:30"


def test_match_decision_prefers_exact_signal_price():
    """Three QQQ fires 10:35/10:40/10:45 and a decision at 10:58 for the 10:30 bar: time
    matching would pin it on 10:35 — the recorded signal price says it belongs to none."""
    ev = [{"kind": "decision", "time": 3480, "direction": "bear", "setup": "vwap_momentum", "price": 655.50},
          {"kind": "decision", "time": 4380, "direction": "bear", "setup": "vwap_momentum", "price": 648.94, "id": "1045"}]
    f1035 = {"time": 2100, "direction": "bear", "setup": "vwap_momentum", "price": 654.95}
    f1045 = {"time": 2700, "direction": "bear", "setup": "vwap_momentum", "price": 648.94}
    assert chart.match_decision(f1035, ev) is None
    assert chart.match_decision(f1045, ev)["id"] == "1045"


def test_match_decision_needs_same_setup_direction_and_window():
    fire = {"time": 1000, "direction": "bear", "setup": "vwap_momentum", "price": 100.0}
    ev = [{"kind": "decision", "time": 900, "direction": "bear", "setup": "vwap_momentum"},        # before the bar
          {"kind": "decision", "time": 1100, "direction": "bear", "setup": "trend_pullback"},      # other setup
          {"kind": "decision", "time": 1200, "direction": "bull", "setup": "vwap_momentum"},       # other side
          {"kind": "decision", "time": 1300, "direction": "bear", "setup": "vwap_momentum", "id": "ok"},
          {"kind": "decision", "time": 1000 + 1800, "direction": "bear", "setup": "vwap_momentum"}]  # too late
    assert chart.match_decision(fire, ev)["id"] == "ok"
    assert chart.match_decision(fire, ev[:3]) is None
