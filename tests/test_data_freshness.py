"""2026-11-17 data-freshness fixes: stale daily cache, delayed SIP bars, clock blips."""
from datetime import datetime, timedelta

import pytz

from spyqqq import config, data

ET = pytz.timezone("America/New_York")


class FakeBroker:
    def __init__(self, daily=(), sip=(), iex=()):
        self._daily, self._sip, self._iex = list(daily), list(sip), list(iex)
        self.calls = []

    def daily_bars(self, sym, days=260):
        return self._daily

    def intraday_bars(self, sym, start, end=None, minutes=5, feed=None):
        self.calls.append(feed)
        rows = self._iex if feed == "iex" else self._sip
        return list(rows)


def _row(ts, close=100.0, vol=1000.0):
    return {"ts": ts, "open": close, "high": close, "low": close, "close": close, "volume": vol}


def test_daily_excludes_today_and_takes_fresh_days(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BARS_CACHE_DIR", tmp_path)
    today = datetime.now(ET).date()
    # cache frozen five weeks ago, plus a partial "today" row that must be purged
    stale = today - timedelta(days=35)
    (tmp_path / "SPY_1d.jsonl").write_text(
        f'{{"ts": "{stale}", "open": 1, "high": 1, "low": 1, "close": 778.57, "volume": 1}}\n'
        f'{{"ts": "{today}", "open": 1, "high": 1, "low": 1, "close": 1.0, "volume": 1}}\n')
    yday = ET.localize(datetime.combine(today - timedelta(days=1), datetime.min.time()))
    now_bar = ET.localize(datetime.combine(today, datetime.min.time()))
    md = data.MarketData(FakeBroker(daily=[_row(yday, 739.95), _row(now_bar, 732.0)]))
    closes = md.daily_closes("SPY")
    assert closes[-1] == 739.95                      # yesterday, not Oct 9 and not today's partial
    assert 1.0 not in closes and 732.0 not in closes
    assert str(today) not in (tmp_path / "SPY_1d.jsonl").read_text()


def test_today_bars_patches_delayed_sip_with_scaled_iex(monkeypatch):
    now = ET.localize(datetime.combine(datetime.now(ET).date(), datetime.min.time())).replace(hour=11, minute=23)
    monkeypatch.setattr(data, "datetime", type("D", (datetime,), {"now": classmethod(lambda c, tz=None: now)}))
    open_ = now.replace(hour=9, minute=30)
    ts = [open_ + timedelta(minutes=5 * i) for i in range(23)]          # 09:30 … 11:20 (forming)
    sip = [_row(t, 100 + i, 100_000.0) for i, t in enumerate(ts) if t <= now - timedelta(minutes=16)]
    iex = [_row(t, 100 + i, 1_000.0) for i, t in enumerate(ts)]
    md = data.MarketData(FakeBroker(sip=sip, iex=iex))
    bars = md.today_bars("SPY")
    assert bars[-1].ts == now.replace(minute=15)                         # real time: 11:15 closed bar
    assert len(bars) == 22 and len(sip) < 22                             # SIP alone would stop ~11:05
    assert all(abs(b.volume - 100_000.0) < 1e-6 for b in bars)           # IEX rescaled to SIP size


def test_today_bars_without_ratio_stays_on_sip(monkeypatch):
    now = ET.localize(datetime.combine(datetime.now(ET).date(), datetime.min.time())).replace(hour=9, minute=40)
    monkeypatch.setattr(data, "datetime", type("D", (datetime,), {"now": classmethod(lambda c, tz=None: now)}))
    iex = [_row(now.replace(minute=30), 100, 500.0)]
    md = data.MarketData(FakeBroker(sip=[], iex=iex))
    assert md.today_bars("SPY") == []        # no overlap to size IEX volume → don't fake it


def test_clock_fallback_keeps_session_open(monkeypatch):
    from spyqqq import engine as eng
    e = eng.Engine.__new__(eng.Engine)
    now = datetime.now(ET)
    e._last_good_clock = {"is_open": True, "next_open": now + timedelta(hours=20),
                          "next_close": now + timedelta(hours=2), "now": now}
    c = e._clock_fallback()
    assert c and c["is_open"] is True
    e._last_good_clock = {"is_open": True, "next_open": now + timedelta(hours=20),
                          "next_close": now - timedelta(minutes=1), "now": now}
    assert e._clock_fallback()["is_open"] is False
