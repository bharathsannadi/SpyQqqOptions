"""data.py — bars, vols and the local IV history, with a disk cache.

The engine asks for: today's 5-minute bars (live scan), ~120 sessions of
5-minute history (confidence replay), daily closes (trend + HV), ATM IV30
(from the chain snapshot), and an IV rank once we've logged enough sessions.
Nothing here places orders.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

import pytz

from . import config, vol_edge
from .signal import Bar

log = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

HISTORY_DAYS = 1500        # all of it: Polygon seed (2021-06) + Alpaca tail


def _to_et(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = pytz.utc.localize(ts)
    return ts.astimezone(ET)


def _bar(d: dict) -> Bar:
    return Bar(ts=_to_et(d["ts"]), open=float(d["open"]), high=float(d["high"]),
               low=float(d["low"]), close=float(d["close"]), volume=float(d["volume"]))


class MarketData:
    def __init__(self, broker):
        self.b = broker
        self._daily: dict[str, tuple[date, list[dict]]] = {}
        self._hist: dict[str, tuple[date, dict[date, list[Bar]]]] = {}

    # ── daily ────────────────────────────────────────────────────────────────
    def daily(self, sym: str) -> list[dict]:
        """Daily bars: the on-disk cache (seeded from Polygon back to 2021 by
        scripts/import_polygon.py) merged with Alpaca's recent bars, persisted."""
        today = datetime.now(ET).date()
        cached = self._daily.get(sym)
        if cached and cached[0] == today:
            return cached[1]
        path = config.BARS_CACHE_DIR / f"{sym}_1d.jsonl"
        by_date: dict[str, dict] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    d = json.loads(line); by_date[d["ts"][:10]] = d
                except Exception:
                    continue
        fresh = self.b.daily_bars(sym, 260)
        added = 0
        for r in fresh:
            k = _to_et(r["ts"]).strftime("%Y-%m-%d")
            if k not in by_date:
                by_date[k] = {"ts": k, "open": r["open"], "high": r["high"], "low": r["low"],
                              "close": r["close"], "volume": r["volume"]}
                added += 1
        if added and by_date:
            tmp = path.with_suffix(".tmp")
            with tmp.open("w") as f:
                for k in sorted(by_date):
                    f.write(json.dumps(by_date[k]) + "\n")
            tmp.replace(path)
        rows = []
        for k in sorted(by_date):
            d = dict(by_date[k]); d["ts"] = ET.localize(datetime.strptime(k, "%Y-%m-%d"))
            rows.append(d)
        if rows:
            self._daily[sym] = (today, rows)
        return rows

    def daily_closes(self, sym: str, before: Optional[date] = None) -> list[float]:
        rows = self.daily(sym)
        if before is not None:
            rows = [r for r in rows if r["ts"].date() < before]
        return [float(r["close"]) for r in rows]

    def daily_by_date(self, sym: str, dates: list[date]) -> dict[date, list[float]]:
        rows = self.daily(sym)
        out = {}
        for d in dates:
            out[d] = [float(r["close"]) for r in rows if r["ts"].date() < d]
        return out

    def hv(self, sym: str) -> tuple[Optional[float], Optional[float]]:
        closes = self.daily_closes(sym)
        return vol_edge.hv_annualized(closes, 5), vol_edge.hv_annualized(closes, 30)

    def daily_move_pcts(self, sym: str, n: int = 6) -> list[float]:
        c = self.daily_closes(sym)
        return [(c[i] - c[i - 1]) / c[i - 1] * 100 for i in range(max(1, len(c) - n), len(c))]

    # ── intraday ─────────────────────────────────────────────────────────────
    def today_bars(self, sym: str) -> list[Bar]:
        now = datetime.now(ET)
        start = now.replace(hour=9, minute=30, second=0, microsecond=0)
        rows = self.b.intraday_bars(sym, start=start.astimezone(pytz.utc))
        bars = [_bar(r) for r in rows]
        # drop the still-forming bar so the rule sees closed bars only
        cutoff = now - timedelta(minutes=5)
        return [x for x in bars if x.ts <= cutoff and x.ts.time() >= start.time()]

    def prev_session_bars(self, sym: str) -> list[Bar]:
        """The last completed session's 5-min bars (EMA/MACD warm-up for vwap_momentum)."""
        hist = self.history(sym)
        today = datetime.now(ET).date()
        prior = [d for d in sorted(hist) if d < today]
        return hist[prior[-1]] if prior else []

    def history(self, sym: str, days: int = HISTORY_DAYS) -> dict[date, list[Bar]]:
        """{session_date: [5-min bars]} for the last `days` sessions, disk-cached."""
        today = datetime.now(ET).date()
        cached = self._hist.get(sym)
        if cached and cached[0] == today:          # refreshed once per session, like daily()
            return cached[1]
        path = config.BARS_CACHE_DIR / f"{sym}_5m.jsonl"
        by_day: dict[date, list[Bar]] = defaultdict(list)
        last_ts: Optional[datetime] = None
        seen: set[str] = set()
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    d = json.loads(line)
                    if d["ts"] in seen:
                        continue
                    seen.add(d["ts"])
                    d["ts"] = datetime.fromisoformat(d["ts"])
                    b = _bar(d)
                    by_day[b.ts.date()].append(b)
                    last_ts = b.ts if last_ts is None or b.ts > last_ts else last_ts
                except Exception:
                    continue
        today = datetime.now(ET).date()
        start = (last_ts + timedelta(minutes=5)) if last_ts else \
            ET.localize(datetime.combine(today - timedelta(days=int(days * 1.5)), datetime.min.time()))
        end = ET.localize(datetime.combine(today, datetime.min.time()))   # yesterday's close
        new_rows: list[dict] = []
        cursor = start
        while cursor < end:                       # the API caps at 10,000 bars per call
            chunk = self.b.intraday_bars(sym, start=cursor.astimezone(pytz.utc),
                                         end=end.astimezone(pytz.utc))
            if not chunk:
                break
            new_rows.extend(chunk)
            last = _to_et(chunk[-1]["ts"])
            if len(chunk) < 10000 or last <= cursor:
                break
            cursor = last + timedelta(minutes=5)
        if new_rows:
            with path.open("a") as f:
                for r in new_rows:
                    b = _bar(r)
                    by_day[b.ts.date()].append(b)
                    f.write(json.dumps({"ts": b.ts.isoformat(), "open": b.open, "high": b.high,
                                        "low": b.low, "close": b.close, "volume": b.volume}) + "\n")
            log.info(f"[data] {sym}: cached {len(new_rows)} new 5-min bars")
        # regular session only, last `days` sessions
        out = {}
        for d in sorted(by_day)[-days:]:
            bars = [x for x in by_day[d] if "09:30" <= x.ts.strftime("%H:%M") <= "16:00"]
            if len(bars) >= 30:
                out[d] = sorted(bars, key=lambda x: x.ts)
        self._hist[sym] = (today, out)
        return out

    # ── implied vol ──────────────────────────────────────────────────────────
    def iv30(self, sym: str, spot: float) -> Optional[float]:
        """ATM implied vol (%) from the contract nearest 30 DTE and nearest the spot."""
        today = datetime.now(ET).date()
        chain = self.b.chain(sym, "call", today + timedelta(days=20), today + timedelta(days=45),
                             spot * 0.97, spot * 1.03)
        with_iv = [c for c in chain if c.iv]
        if not with_iv:
            return None
        best = min(with_iv, key=lambda c: (abs((c.expiry - today).days - 30), abs(c.strike - spot)))
        return round(best.iv * 100.0, 2)

    def record_iv(self, sym: str, iv: Optional[float]) -> None:
        if iv is None:
            return
        today = datetime.now(ET).strftime("%Y-%m-%d")
        rows = self._iv_rows(sym)
        if rows and rows[-1]["date"] == today:
            return
        with config.IV_HISTORY_PATH.open("a") as f:
            f.write(json.dumps({"date": today, "sym": sym, "iv30": iv}) + "\n")

    def _iv_rows(self, sym: str) -> list[dict]:
        if not config.IV_HISTORY_PATH.exists():
            return []
        out = []
        for line in config.IV_HISTORY_PATH.read_text().splitlines():
            try:
                d = json.loads(line)
                if d.get("sym") == sym:
                    out.append(d)
            except Exception:
                pass
        return out

    def iv_rank(self, sym: str, iv: Optional[float]) -> Optional[float]:
        hist = [r["iv30"] for r in self._iv_rows(sym)]
        if len(hist) < config.IV_HISTORY_MIN_DAYS:
            return None
        return vol_edge.iv_rank(iv, hist)
