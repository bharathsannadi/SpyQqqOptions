"""chart.py — candles + signal markers for the dashboard chart. Read-only.

Bars: the on-disk 5-min cache for past sessions; for today, the same SIP+IEX
merge the engine uses (MarketData.today_bars) through a data-only Alpaca client —
this module can't place orders. Signals are a bar-by-bar replay of the exact rule
in signal.py (same function the live scan and the confidence replay use), so a
marker means "the rule fired on this closed bar". Ledger rows (decisions, fills,
closes) are overlaid so you can see which fires the gates let through.
"""
from __future__ import annotations

import json
import logging
import threading
import time as _time
from array import array
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Optional

import pytz

from . import config
from . import signal as sig
from .data import ET, SIP_DELAY_MIN, MarketData, _to_et
from .ledger import Ledger

log = logging.getLogger(__name__)

_lock = threading.Lock()
_hist: dict = {}                                                   # sym -> (mtime, _Days)
_daily: dict[str, tuple[float, list[tuple[str, float]]]] = {}
_today: dict[str, tuple[float, list[sig.Bar]]] = {}                # sym -> (fetched_at, bars)
TODAY_TTL = 30


class _DataOnly:
    """Just `intraday_bars`, over Alpaca's market-data REST API with `requests`.
    Read-only by construction (no trading endpoint is ever called), and it keeps the
    dashboard light: importing alpaca-py's SDK alone costs the web process ~85 MB."""
    URL = "https://data.alpaca.markets/v2/stocks/{sym}/bars"

    def __init__(self):
        from .credentials import load_alpaca_creds
        c = load_alpaca_creds()
        self.headers = {"APCA-API-KEY-ID": c.key, "APCA-API-SECRET-KEY": c.secret} if c.is_complete else None

    def intraday_bars(self, sym, start, end=None, minutes=5, feed=None):
        if self.headers is None:
            return []
        import requests
        fmt = lambda t: t.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {"timeframe": f"{minutes}Min", "start": fmt(start), "limit": 10000, "adjustment": "raw"}
        if end is not None:
            params["end"] = fmt(end)
        if feed:
            params["feed"] = feed
        out = []
        try:
            for _ in range(20):                                   # pagination guard
                r = requests.get(self.URL.format(sym=sym), params=params, headers=self.headers, timeout=10)
                r.raise_for_status()
                js = r.json()
                for b in js.get("bars") or []:
                    out.append({"ts": datetime.fromisoformat(b["t"].replace("Z", "+00:00")), "open": b["o"],
                                "high": b["h"], "low": b["l"], "close": b["c"], "volume": b["v"]})
                tok = js.get("next_page_token")
                if not tok:
                    break
                params["page_token"] = tok
        except Exception as e:
            log.warning(f"[chart] intraday_bars {sym} ({feed or 'sip'}): {e}")
        return out


_md: Optional[MarketData] = None


def _market_data() -> MarketData:
    global _md
    if _md is None:
        _md = MarketData(_DataOnly())
    return _md


class _Days(Mapping):
    """{session_date: [Bar]} over packed float arrays — Bars are built only for the
    days a caller actually touches. Five years of SPY+QQQ 5-min bars as Bar objects
    cost ~190 MB; packed they are ~12 MB."""
    def __init__(self, packed: dict):
        self._p = packed

    def __getitem__(self, d: date) -> list[sig.Bar]:
        a = self._p[d]
        return [sig.Bar(datetime.fromtimestamp(a[i], ET), a[i + 1], a[i + 2], a[i + 3], a[i + 4], a[i + 5])
                for i in range(0, len(a), 6)]

    def __iter__(self):
        return iter(self._p)

    def __len__(self):
        return len(self._p)


def _history(sym: str) -> _Days:
    """Regular-session 5-min bars from the engine's on-disk cache, same filter as
    MarketData.history (09:30–16:00, sessions with ≥30 bars). Re-read when the file changes."""
    path = config.BARS_CACHE_DIR / f"{sym}_5m.jsonl"
    mtime = path.stat().st_mtime if path.exists() else 0
    cached = _hist.get(sym)
    if cached and cached[0] == mtime:
        return cached[1]
    # Stream the file packing one session at a time (it's append-only, so sessions arrive
    # in order); a late row for an already-packed day re-opens that day. Peak memory is
    # ~one session instead of five years of tuples. Duplicate timestamps: last row wins.
    packed: dict[date, array] = {}
    cur_day: Optional[date] = None
    cur: dict[float, tuple] = {}

    def flush():
        if cur_day is not None and cur:
            a = array("d")
            for k in sorted(cur):
                a.extend(cur[k])
            packed[cur_day] = a

    if path.exists():
        with path.open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    t = d["ts"]
                    if not ("09:30" <= t[11:16] <= "16:00"):
                        continue
                    ts = _to_et(datetime.fromisoformat(t))
                    row = (ts.timestamp(), float(d["open"]), float(d["high"]), float(d["low"]),
                           float(d["close"]), float(d["volume"]))
                except Exception:
                    continue
                day = ts.date()
                if day != cur_day:
                    flush()
                    old = packed.pop(day, None)
                    cur = {old[i]: tuple(old[i:i + 6]) for i in range(0, len(old), 6)} if old is not None else {}
                    cur_day = day
                cur[row[0]] = row
    flush()
    packed = {k: a for k, a in packed.items() if len(a) // 6 >= 30}
    out = _Days(packed)
    _hist[sym] = (mtime, out)
    return out


def _daily_closes(sym: str) -> list[tuple[str, float]]:
    path = config.BARS_CACHE_DIR / f"{sym}_1d.jsonl"
    mtime = path.stat().st_mtime if path.exists() else 0
    cached = _daily.get(sym)
    if cached and cached[0] == mtime:
        return cached[1]
    rows: dict[str, float] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                d = json.loads(line); rows[d["ts"][:10]] = float(d["close"])
            except Exception:
                continue
    out = sorted(rows.items())
    _daily[sym] = (mtime, out)
    return out


_fetch_locks = {}


def _today_bars(sym: str) -> list[sig.Bar]:
    """Today's closed bars, re-fetched at most every TODAY_TTL s. The network call runs
    under a per-symbol fetch lock only (de-dupes concurrent refreshes) — never under
    the cache lock, so a slow Alpaca call can't stall the other endpoints."""
    c = _today.get(sym)
    if c and _time.time() - c[0] < TODAY_TTL:
        return c[1]
    lk = _fetch_locks.setdefault(sym, threading.Lock())
    with lk:
        c = _today.get(sym)
        if c and _time.time() - c[0] < TODAY_TTL:        # another request just refreshed it
            return c[1]
        try:
            bars = _market_data().today_bars(sym)
        except Exception as e:
            log.warning(f"[chart] today_bars {sym}: {e}")
            bars = c[1] if c else []
        _today[sym] = (_time.time(), bars)
        return bars


def _epoch(ts: datetime) -> int:
    """Chart time axis: ET wall-clock encoded as if UTC (the chart lib has no tz support)."""
    et = ts.astimezone(ET)
    return int(datetime(et.year, et.month, et.day, et.hour, et.minute, tzinfo=pytz.utc).timestamp())


def sessions(sym: str) -> list[str]:
    with _lock:
        days = sorted(_history(sym))
    today = datetime.now(ET).date()
    out = [d.isoformat() for d in days]
    if not out or out[-1] != today.isoformat():
        out.append(today.isoformat())
    return out[::-1]


def build(sym: str, day: Optional[str] = None) -> dict:
    sym = sym.upper()
    if sym not in config.UNDERLYINGS:
        raise ValueError(f"unknown symbol {sym}")
    now = datetime.now(ET)
    today = now.date()
    d = date.fromisoformat(day) if day else today
    live = d == today
    bars = _today_bars(sym) if live else None            # network: outside the cache lock
    with _lock:
        hist = _history(sym)
        fallback = False
        if live and not bars and (now.weekday() >= 5 or now.time() >= dtime(9, 45)):
            # weekend / holiday / feed down: show the last completed session instead of a blank chart
            past = [k for k in sorted(hist) if k < today]
            if past:
                d, live, fallback = past[-1], False, True
        if not live:
            bars = hist.get(d, [])
        prior = [k for k in sorted(hist) if k < d]
        warmup = hist[prior[-1]] if prior else []
        daily = [c for k, c in _daily_closes(sym) if k < d.isoformat()]
    source = "live" if live else "cache"
    # newest live bars are real-time IEX with rescaled volume until delayed SIP replaces them
    provisional_after = _epoch(now - timedelta(minutes=SIP_DELAY_MIN + 5)) if live else None

    trend = sig.daily_trend(daily)
    lo, hi = config.ENTRY_WINDOW_START_ET, config.ENTRY_WINDOW_END_ET

    # indicators
    closes_all = [b.close for b in warmup] + [b.close for b in bars]
    off = len(warmup)
    e9 = sig.ema_series(closes_all, 9)[off:]
    e21 = sig.ema_series(closes_all, 21)[off:]
    vw = sig.vwap_series(bars)

    # bar-by-bar replay of every setup (enabled ones = what the live scan would have seen)
    signals = []
    for i in range(2, len(bars)):
        for st in sig.SETUPS:
            try:
                s = sig.evaluate(sym, trend, bars[:i + 1], st, warmup)
            except Exception:
                s = None
            if s is None:
                continue
            t = bars[i].ts.time()
            signals.append({"time": _epoch(bars[i].ts), "et": bars[i].ts.strftime("%H:%M"),
                            "direction": s.direction, "setup": st, "strength": s.strength,
                            "price": round(s.price, 2), "in_window": lo <= t <= hi,
                            "traded": st in config.SETUPS_ENABLED,
                            "provisional": bool(provisional_after and _epoch(bars[i].ts) > provisional_after),
                            "indicators": s.indicators})

    # ledger overlay
    events = []
    try:
        for r in Ledger().rows(d.isoformat()):
            if r.get("sym") != sym or r.get("kind") not in ("decision", "fill", "close", "orphan"):
                continue
            ts = datetime.fromisoformat(r["ts"])
            ev = {"time": _epoch(ts), "et": ts.astimezone(ET).strftime("%H:%M"), "kind": r["kind"],
                  "direction": r.get("direction"), "setup": r.get("setup")}
            if r["kind"] == "decision":
                ev["accepted"] = bool(r.get("accepted"))
                failed = [g for g in (r.get("gates") or []) if not g.get("ok")]
                ev["why"] = failed[0].get("text") if failed else (r.get("reason") or "")
                ev["gate"] = failed[0].get("gate") if failed else None
                ev["price"] = (r.get("signal") or {}).get("price")       # the signal bar's close — exact match key
            elif r["kind"] == "fill":
                ev.update(qty=r.get("qty"), net_debit=r.get("net_debit"), dry=bool(r.get("dry")))
            else:
                ev.update(pnl_usd=r.get("pnl_usd"), pnl_pct=r.get("pnl_pct"), reason=r.get("reason"))
            events.append(ev)
    except Exception as e:
        log.warning(f"[chart] ledger overlay: {e}")

    def win_epoch(t):
        return int(datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=pytz.utc).timestamp())

    return {
        "sym": sym, "day": d.isoformat(), "source": source, "fallback": fallback, "trend": trend,
        "setups": list(config.SETUPS_ENABLED), "all_setups": list(sig.SETUPS),
        "window": {"start": win_epoch(lo), "end": win_epoch(hi),
                   "label": f"{lo.strftime('%H:%M')}–{hi.strftime('%H:%M')}"},
        "bars": [{"time": _epoch(b.ts), "open": round(b.open, 3), "high": round(b.high, 3),
                  "low": round(b.low, 3), "close": round(b.close, 3), "volume": round(b.volume)} for b in bars],
        "vwap": [{"time": _epoch(b.ts), "value": round(v, 3)} for b, v in zip(bars, vw)],
        "ema9": [{"time": _epoch(b.ts), "value": round(v, 3)} for b, v in zip(bars, e9) if v is not None],
        "ema21": [{"time": _epoch(b.ts), "value": round(v, 3)} for b, v in zip(bars, e21) if v is not None],
        "signals": signals, "events": events,
    }


# ── confidence for setups the engine isn't trading (display only) ───────────
_extra: dict = {"day": None, "data": {}, "running": False}
_extra_lock = threading.Lock()


def _compute_extra(day: date) -> None:
    from . import history
    out: dict = {}
    try:
        for sym in config.UNDERLYINGS:
            with _lock:
                hist = _history(sym)
                daily = _daily_closes(sym)
            intraday = _Days({d: hist._p[d] for d in hist if d < day})       # still packed; replay unpacks per day
            closes = [(date.fromisoformat(k), c) for k, c in daily]
            by_date = {d: [c for k, c in closes if k < d] for d in intraday}
            for st in sig.SETUPS:
                if st in config.SETUPS_ENABLED:
                    continue
                hr = history.replay(sym, by_date, intraday, st)
                out.setdefault(sym, {})[st] = {k: v.as_dict() for k, v in hr.items()}
        _extra.update(day=day, data=out)
    except Exception as e:
        log.warning(f"[chart] extra history: {e}")
    finally:
        _extra["running"] = False


def history_extra() -> dict:
    """Hit rates for disabled setups, replayed the same way the engine does for
    enabled ones. Computed once per session in a background thread."""
    today = datetime.now(ET).date()
    with _extra_lock:
        start = _extra["day"] != today and not _extra["running"]
        if start:
            _extra["running"] = True
    if start:
        threading.Thread(target=_compute_extra, args=(today,), daemon=True).start()
    return {"ready": _extra["day"] == today, "disabled": [s for s in sig.SETUPS if s not in config.SETUPS_ENABLED],
            "history": _extra["data"]}


# ── "why no signal": every condition of every setup on the latest closed bar ──
def _g(v, t, lo, hi, want, unit=""):
    """Gauge spec for the UI slider: value `v`, threshold `t` on a [lo, hi] scale;
    `want` = 'gt' | 'lt' (which side of t passes) or 'band' (t = [a, b])."""
    return {"v": round(v, 4), "t": t, "lo": lo, "hi": hi, "want": want, "unit": unit}


def _pct(a, b):
    return (a - b) / b * 100 if b else 0.0


def _vwap_checks(direction: str, bars, warmup) -> list[dict]:
    series = list(warmup) + list(bars)
    closes = [b.close for b in series]
    if len(bars) < 3 or len(closes) < 26 + 9 + 1:
        return [{"key": "bars", "label": "Bars", "ok": False, "value": f"{len(bars)}", "need": "≥3 + warm-up",
                 "tip": "Not enough closed bars yet today", "g": _g(len(bars), 3, 0, 6, "gt")}]
    e9, e21 = sig.ema_series(closes, 9)[-1], sig.ema_series(closes, 21)[-1]
    mh = sig.macd_hist(closes)
    vws = sig.vwap_series(bars)
    vw = vws[-1]
    above = sum(1 for b, v in zip(bars[-30:], vws[-30:]) if b.close > v) / min(len(bars), 30)
    vols = [b.volume for b in series[-21:-1]]
    vr = series[-1].volume / (sum(vols) / len(vols)) if vols and sum(vols) > 0 else 0.0
    cur, prev = series[-1], series[-2]
    bull = direction == "bull"
    want = "gt" if bull else "lt"
    need_vr = config.VWAP_MIN_VOL_RATIO
    return [
        {"key": "vwap", "label": "vs VWAP", "ok": (cur.close > vw) if bull else (cur.close < vw),
         "value": f"{_pct(cur.close, vw):+.2f}%", "need": f"{'>' if bull else '<'} 0",
         "tip": f"Close {cur.close:.2f} vs VWAP {vw:.2f} — must be {'above' if bull else 'below'}",
         "g": _g(_pct(cur.close, vw), 0, -0.6, 0.6, want, "%")},
        {"key": "above", "label": "Time > VWAP", "ok": (above > .5) if bull else (above < .5),
         "value": f"{above * 100:.0f}%", "need": f"{'>' if bull else '<'} 50%",
         "tip": f"{above * 100:.0f}% of the last 30 bars closed above VWAP — must be {'over' if bull else 'under'} 50%",
         "g": _g(above * 100, 50, 0, 100, want, "%")},
        {"key": "ema", "label": "EMA 9 / 21", "ok": (e9 > e21) if bull else (e9 < e21),
         "value": f"{_pct(e9, e21):+.3f}%", "need": f"{'>' if bull else '<'} 0",
         "tip": f"EMA 9 {e9:.2f} vs EMA 21 {e21:.2f} — 9 must be {'above' if bull else 'below'} 21",
         "g": _g(_pct(e9, e21), 0, -0.25, 0.25, want, "%")},
        {"key": "macd", "label": "MACD", "ok": (mh > 0) if bull else (mh < 0),
         "value": f"{mh:+.3f}", "need": f"{'>' if bull else '<'} 0",
         "tip": f"MACD(12,26,9) histogram {mh:+.3f} — must be {'positive' if bull else 'negative'}",
         "g": _g(mh / cur.close * 100, 0, -0.04, 0.04, want, "%")},
        {"key": "bar", "label": "Bar", "ok": (cur.close > prev.close) if bull else not (cur.close > prev.close),
         "value": f"{cur.close - prev.close:+.2f}", "need": "up" if bull else "down",
         "tip": f"This bar closed {cur.close - prev.close:+.2f} vs the prior bar — must close {'up' if bull else 'down/flat'}",
         "g": _g(_pct(cur.close, prev.close), 0, -0.25, 0.25, "gt" if bull else "le", "%")},
        {"key": "vol", "label": "Volume", "ok": vr >= need_vr,
         "value": f"{vr:.2f}×", "need": f"≥ {need_vr:.1f}×",
         "tip": f"Bar volume {vr:.2f}× the trailing 20-bar average — needs ≥ {need_vr:.1f}×",
         "g": _g(vr, need_vr, 0, 2.5, "ge", "×")},
    ]


def _pullback_checks(direction: str, bars) -> list[dict]:
    n_need = max(config.PULLBACK_EMA, config.RSI_LEN) + 2
    if len(bars) < n_need:
        return [{"key": "bars", "label": "Bars", "ok": False, "value": f"{len(bars)}", "need": f"≥ {n_need}",
                 "tip": "Not enough closed bars yet today", "g": _g(len(bars), n_need, 0, n_need * 2, "ge")}]
    closes = [b.close for b in bars]
    ema = sig.ema_series(closes, config.PULLBACK_EMA)
    e_prev, e_now = ema[-2], ema[-1]
    r = sig.rsi(closes, config.RSI_LEN)
    cur, prev = bars[-1], bars[-2]
    vols = [b.volume for b in bars[-7:-1]]
    avg = sum(vols) / len(vols) if vols else 0.0
    vr = cur.volume / avg if avg > 0 else 0.0
    bull = direction == "bull"
    lo, hi = (config.RSI_MIN, config.RSI_MAX) if bull else (100 - config.RSI_MAX, 100 - config.RSI_MIN)
    reclaim_ok = (cur.close > e_now and cur.close > cur.open) if bull else (cur.close < e_now and cur.close < cur.open)
    return [
        {"key": "pullback", "label": "Pullback", "ok": (prev.close < e_prev) if bull else (prev.close > e_prev),
         "value": f"{_pct(prev.close, e_prev):+.2f}%", "need": f"{'<' if bull else '>'} 0",
         "tip": f"Prior bar {prev.close:.2f} vs EMA 9 {e_prev:.2f} — must have pulled back {'below' if bull else 'above'} it",
         "g": _g(_pct(prev.close, e_prev), 0, -0.3, 0.3, "lt" if bull else "gt", "%")},
        {"key": "reclaim", "label": "Reclaim", "ok": reclaim_ok,
         "value": f"{_pct(cur.close, e_now):+.2f}%", "need": f"{'>' if bull else '<'} 0, {'green' if bull else 'red'}",
         "tip": f"This bar {cur.close:.2f} vs EMA 9 {e_now:.2f}, {'green' if cur.close > cur.open else 'red'} candle — must close back {'above' if bull else 'below'} on a {'green' if bull else 'red'} bar",
         "g": _g(_pct(cur.close, e_now), 0, -0.3, 0.3, "gt" if bull else "lt", "%")},
        {"key": "rsi", "label": "RSI 14", "ok": r is not None and lo <= r <= hi,
         "value": "—" if r is None else f"{r:.0f}", "need": f"{lo}–{hi}",
         "tip": f"RSI 14 must be inside the no-chase band {lo}–{hi}",
         "g": _g(r if r is not None else 50, [lo, hi], 0, 100, "band")},
        {"key": "vol", "label": "Volume", "ok": vr >= config.VOL_RATIO_MIN, "value": f"{vr:.2f}×",
         "need": f"≥ {config.VOL_RATIO_MIN}×",
         "tip": f"Bar volume {vr:.2f}× the prior 30 min — needs ≥ {config.VOL_RATIO_MIN}×",
         "g": _g(vr, config.VOL_RATIO_MIN, 0, 2.5, "ge", "×")},
    ]


def match_decision(sig_row: dict, events: list[dict]) -> Optional[dict]:
    """The engine's decision for a fire. Decisions record the signal bar's close, so match
    on setup + direction + that price (exact). Only rows without a price fall back to the
    time rule (logged within 30 min after the bar) — several decisions can sit in one window."""
    cands = [e for e in events if e["kind"] == "decision" and e.get("direction") == sig_row["direction"]
             and e.get("setup") == sig_row["setup"] and sig_row["time"] <= e["time"] < sig_row["time"] + 3600]
    for e in cands:
        if e.get("price") is not None and abs(e["price"] - sig_row["price"]) < 0.015:
            return e
    for e in cands:
        if e.get("price") is None and e["time"] < sig_row["time"] + 1800:
            return e
    return None


def diagnose(sym: str) -> dict:
    """Every condition of every setup on the latest closed bar, plus today's fires."""
    sym = sym.upper()
    d = build(sym)
    if d["source"] != "live":
        return {"sym": sym, "trend": d["trend"], "bar": None, "live": False, "setups": [],
                "fires_today": 0, "fires_in_window": 0, "last_fire": None, "last_decision": None}
    bars = _today_bars(sym)
    with _lock:
        hist = _history(sym)
        prior = [k for k in sorted(hist) if k < date.fromisoformat(d["day"])]
        warmup = hist[prior[-1]] if prior else []
    trend = d["trend"]
    setups = []
    for st in sig.SETUPS:
        need_trend = sig.trend_required(st)
        dirs = [trend] if (need_trend and trend) else ([] if need_trend else ["bull", "bear"])
        block = {"setup": st, "traded": st in config.SETUPS_ENABLED, "needs_trend": need_trend, "directions": []}
        if need_trend and not trend:
            block["blocked"] = "daily trend is flat — this setup needs a trend"
        for dr in dirs:
            checks = _vwap_checks(dr, bars, warmup) if st == "vwap_momentum" else _pullback_checks(dr, bars)
            block["directions"].append({"direction": dr, "checks": checks, "passed": sum(c["ok"] for c in checks),
                                        "total": len(checks)})
        setups.append(block)
    fires = [s for s in d["signals"] if s["traded"]]
    last = fires[-1] if fires else None
    decided = [(f, m) for f in fires for m in [match_decision(f, d["events"])] if m]
    last_fire_dec, last_dec = decided[-1] if decided else (None, None)
    return {"sym": sym, "trend": trend, "bar": bars[-1].ts.strftime("%H:%M") if bars else None, "live": True,
            "setups": setups, "fires_today": len(fires),
            "fires_in_window": sum(1 for s in fires if s["in_window"]),
            "decided_today": len(decided),
            "last_fire": last,
            # the newest fire the engine actually decided on (the newest fire may have none)
            "last_decided_fire": last_fire_dec, "last_decision": last_dec}
