"""engine.py — the loop. One thread, no UI state, no eventlet.

connect (retry forever) → reconcile → every tick: heartbeat, status file,
monitor open spreads (10s), scan for entries (60s, inside the window),
flatten at 15:45, write the EOD report once, sleep to the next session.
"""
from __future__ import annotations

import logging
import signal as _signal
import time as _time
import uuid
from datetime import datetime, timedelta
from typing import Optional

import pytz

from . import config, contracts, debate, exits, gates, history, report, signal as sig
from .broker import Broker
from .data import MarketData
from .ledger import Ledger
from .risk import DayState
from .status import write_status

log = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


class Engine:
    def __init__(self, dry_run: bool = False):
        config.DRY_RUN = config.DRY_RUN or dry_run
        self.broker = Broker()
        self.ledger = Ledger()
        self.md: Optional[MarketData] = None
        self.day = DayState.load()
        self.open: dict[str, exits.OpenSpread] = {}
        self.exit_engine = exits.new_engine()
        self.hit_rates: dict[str, dict[str, history.HitRate]] = {}
        self.last_signal: dict[str, dict] = {}
        self._last_scan = self._last_monitor = 0.0
        self._last_reject: dict[tuple, float] = {}
        self._reported_for: str = ""
        self._stop = False
        self._clock_cache: tuple[float, Optional[dict]] = (0.0, None)
        self.account: dict = {}
        _signal.signal(_signal.SIGTERM, lambda *_: self.stop())
        _signal.signal(_signal.SIGINT, lambda *_: self.stop())

    def stop(self):
        log.info("[engine] stop requested")
        self._stop = True

    # ── lifecycle ────────────────────────────────────────────────────────────
    def run(self) -> None:
        self.ledger.engine("boot", dry_run=config.DRY_RUN)
        self.broker.connect(forever=True)
        self.md = MarketData(self.broker)
        self.account = self.broker.account() or {}
        self.reconcile()
        self.refresh_history()
        self.ledger.engine("ready", equity=self.account.get("equity"), open=len(self.open))
        while not self._stop:
            try:
                self.tick()
            except Exception as e:
                log.exception(f"[engine] tick error: {e}")
                self.ledger.engine("tick_error", error=f"{type(e).__name__}: {e}")
                if not self.broker.alive():
                    log.warning("[engine] broker dead — reconnecting")
                    self.broker.connect(forever=True)
            _time.sleep(1.0)
        self.ledger.engine("shutdown")

    def beat(self) -> None:
        config.HEARTBEAT_PATH.write_text(str(_time.time()))

    def now(self) -> datetime:
        return datetime.now(ET)

    def clock(self) -> Optional[dict]:
        ts, c = self._clock_cache
        if _time.time() - ts < 30:
            return c
        c = self.broker.clock()
        self._clock_cache = (_time.time(), c)
        return c

    # ── one iteration ────────────────────────────────────────────────────────
    def tick(self) -> None:
        self.beat()
        now = self.now()
        self.rollover(now)
        clk = self.clock()
        market_open = bool(clk and clk["is_open"])
        t = _time.time()

        if market_open and t - self._last_monitor >= config.MONITOR_INTERVAL_SEC:
            self._last_monitor = t
            self.monitor(now)

        if market_open and exits.is_flatten_time(now):
            self.flatten_all("EOD flatten")
            if self._reported_for != self.day.date and now.time() >= config.FLATTEN_AT_ET:
                self.write_report()

        if (market_open and t - self._last_scan >= config.SCAN_INTERVAL_SEC
                and gates.entry_window(now)[0]):
            self._last_scan = t
            for sym in config.UNDERLYINGS:
                self.scan(sym, now)

        if t - getattr(self, "_last_status", 0) >= 5:
            self._last_status = t
            if t - getattr(self, "_last_acct", 0) >= 60:
                self._last_acct = t
                self.account = self.broker.account() or self.account
            write_status(self, clk)

        if not market_open:
            _time.sleep(14)          # 15s cadence off-hours; heartbeat still fresh

    def rollover(self, now: datetime) -> None:
        if self.day.date != now.strftime("%Y-%m-%d"):
            log.info(f"[engine] new session {now:%Y-%m-%d}")
            self.day = DayState()
            self.day.save()
            self.hit_rates = {}
            self.refresh_history()

    # ── history / confidence ─────────────────────────────────────────────────
    def refresh_history(self) -> None:
        for sym in config.UNDERLYINGS:
            try:
                intraday = self.md.history(sym)
                daily = self.md.daily_by_date(sym, sorted(intraday))
                self.hit_rates[sym] = history.replay(sym, daily, intraday)
                for d, hr in self.hit_rates[sym].items():
                    log.info(f"[history] {sym} {d}: {hr.label()}")
            except Exception as e:
                log.warning(f"[history] {sym}: {e}")

    # ── scan ─────────────────────────────────────────────────────────────────
    def scan(self, sym: str, now: datetime) -> None:
        trend = sig.daily_trend(self.md.daily_closes(sym))
        bars = self.md.today_bars(sym)
        s = sig.evaluate(sym, trend, bars)
        snap = {"ts": now.isoformat(timespec="seconds"), "trend": trend, "bars": len(bars),
                "price": bars[-1].close if bars else None, "signal": None, "gates": [],
                "confidence": None, "candidate": None, "debate": None, "accepted": False}
        if bars:
            # always expose the indicator state, signal or not
            closes = [b.close for b in bars]
            e = sig.ema_series(closes, config.PULLBACK_EMA)[-1]
            snap["indicators"] = {"ema9": round(e, 2) if e else None,
                                  "rsi14": round(sig.rsi(closes, config.RSI_LEN) or 0, 1)}
        hr_all = self.hit_rates.get(sym, {})
        snap["history"] = {d: h.as_dict() for d, h in hr_all.items()}
        if s is None:
            self.last_signal[sym] = snap
            return
        snap["signal"] = {"direction": s.direction, "setup": s.setup, "strength": s.strength,
                          "price": s.price, "indicators": s.indicators}
        g: list[dict] = [{"gate": "entry_window", **dict(zip(("ok", "text"), gates.entry_window(now)))}]
        hr = hr_all.get(s.direction)
        snap["confidence"] = hr.as_dict() if hr else None
        g.append({"gate": "confidence", **dict(zip(("ok", "text"), gates.confidence_gate(hr)))})
        s.hv5, s.hv30 = self.md.hv(sym)
        s.iv30 = self.md.iv30(sym, s.price)
        self.md.record_iv(sym, s.iv30)
        s.ivr = self.md.iv_rank(sym, s.iv30)
        g.append({"gate": "vol_edge", **dict(zip(("ok", "text"), gates.vol_edge_gate(s.hv5, s.hv30, s.iv30)))})
        cand = None
        if all(x["ok"] for x in g):
            cand = contracts.select(self.broker, sym, s.direction, s.price, now.date())
            if cand is None:
                g.append({"gate": "chain", "ok": False, "text": f"KB §9: no {sym} chain inside DTE window"})
            else:
                g.extend(cand.gates)
                snap["candidate"] = cand.as_dict()
        qty = 0
        if cand and all(x["ok"] for x in g):
            qty = self.day.contracts_for(cand.net_debit)
            debit_usd = cand.net_debit * 100 * max(qty, 1)
            ok, text = self.day.can_enter(sym, debit_usd) if qty else (False, f"KB §4: one spread costs ${cand.net_debit*100:,.0f} > room left")
            g.append({"gate": "day_risk", "ok": ok, "text": text})
        if cand and all(x["ok"] for x in g):
            res = debate.run(s, {"candidate": f"{cand.long.occ}/{cand.short.occ} debit {cand.net_debit:.2f}",
                                 "qty": qty, "max_loss_usd": round(cand.net_debit * 100 * qty, 2)})
            snap["debate"] = {k: res[k] for k in ("proceed", "confidence", "reason", "status")}
            g.append({"gate": "debate", **dict(zip(("ok", "text"), gates.debate_gate(res)))})
        snap["gates"] = g
        accepted = bool(cand) and all(x["ok"] for x in g)
        snap["accepted"] = accepted
        self.last_signal[sym] = snap
        self._log_decision(sym, s, accepted, g, hr, snap.get("debate"), cand, qty)
        if accepted:
            self.enter(s, cand, qty, now)

    def _log_decision(self, sym, s, accepted, g, hr, deb, cand, qty) -> None:
        failed = [x["text"] for x in g if not x["ok"]]
        key = (sym, s.direction, failed[0][:40] if failed else "accepted")
        if not accepted and _time.time() - self._last_reject.get(key, 0) < 900:
            return                     # same rejection within 15 min — don't spam the ledger
        self._last_reject[key] = _time.time()
        self.ledger.decision(sym, s.setup, s.direction, accepted, g,
                             confidence=hr.as_dict() if hr else None, debate=deb,
                             signal={"strength": s.strength, "price": s.price, **s.indicators},
                             vol={"hv5": s.hv5, "hv30": s.hv30, "iv30": s.iv30, "ivr": s.ivr},
                             candidate=cand.as_dict() if cand else None, qty=qty)

    # ── enter / monitor / close ──────────────────────────────────────────────
    def enter(self, s: sig.Signal, cand: contracts.SpreadCandidate, qty: int, now: datetime) -> None:
        pos_id = uuid.uuid4().hex[:10]
        net_limit = round(cand.net_mid + max(0.01, config.LIMIT_PAD_FRAC * cand.half_spreads), 2)
        if config.DRY_RUN:
            log.info(f"[engine] DRY would open {cand.long.occ}/{cand.short.occ} x{qty} ≤{net_limit:.2f}")
            self.ledger.fill(s.sym, s.setup, pos_id=pos_id, direction=s.direction, qty=qty,
                             long_occ=cand.long.occ, short_occ=cand.short.occ, net_debit=net_limit,
                             expected_mid=cand.net_mid, slippage=0.0, order_id="dry", dry=True)
            self._track(pos_id, s, cand, qty, net_limit, now)
            return
        oid = self.broker.open_spread(cand.long.occ, cand.short.occ, qty, net_limit)
        if not oid:
            self.ledger.engine("order_rejected", sym=s.sym, candidate=cand.as_dict())
            return
        fill = self.broker.wait_fill(oid)
        if fill["status"] == "pending":
            self.broker.cancel(oid)
            _time.sleep(2)
            fill = self.broker.wait_fill(oid, timeout=6)
        if fill["status"] in ("rejected", "pending") and fill["qty"] == 0:
            self.ledger.engine("order_unfilled", sym=s.sym, order_id=oid, raw=fill["raw"], limit=net_limit)
            return
        price = fill["price"] or net_limit
        filled_qty = fill["qty"] or qty
        self.ledger.fill(s.sym, s.setup, pos_id=pos_id, direction=s.direction, qty=filled_qty,
                         long_occ=cand.long.occ, short_occ=cand.short.occ, net_debit=price,
                         expected_mid=cand.net_mid, slippage=round(price - cand.net_mid, 3),
                         order_id=oid, raw=fill["raw"])
        self._track(pos_id, s, cand, filled_qty, price, now)

    def _track(self, pos_id, s, cand, qty, price, now) -> None:
        st = self.exit_engine.init_position(entry=price)
        self.open[pos_id] = exits.OpenSpread(pos_id, s.sym, s.direction, s.setup, cand.long.occ,
                                             cand.short.occ, qty, price, now, st)
        self.day.register_entry(s.sym, pos_id, price * 100 * qty)
        log.info(f"[engine] OPEN {s.sym} {s.direction} {cand.long.occ}/{cand.short.occ} x{qty} @ {price:.2f}")

    def monitor(self, now: datetime) -> None:
        for pos_id, pos in list(self.open.items()):
            mid = self.broker.spread_mid(pos.long_occ, pos.short_occ)
            if mid is None:
                continue
            action, why = exits.check(pos, mid, now, self.exit_engine)
            if action == "close":
                self.close(pos, mid, why)

    def close(self, pos: exits.OpenSpread, mid: float, why: str) -> None:
        net_limit = round(max(mid - config.LIMIT_PAD_FRAC * 0.10, 0.01), 2)
        if config.DRY_RUN:
            price = mid
        else:
            oid = self.broker.close_spread(pos.long_occ, pos.short_occ, pos.qty, net_limit)
            if not oid:
                return
            fill = self.broker.wait_fill(oid)
            if fill["status"] == "pending":
                self.broker.cancel(oid)
                # second attempt at a worse price so we are flat
                oid = self.broker.close_spread(pos.long_occ, pos.short_occ, pos.qty, round(mid * 0.90, 2))
                fill = self.broker.wait_fill(oid) if oid else fill
            if fill["qty"] == 0:
                self.ledger.engine("close_unfilled", pos_id=pos.pos_id, sym=pos.sym, reason=why)
                return
            price = fill["price"] or net_limit
        pnl = (price - pos.net_debit) * 100 * pos.qty
        self.ledger.close(pos.sym, pos.setup, pos_id=pos.pos_id, direction=pos.direction, qty=pos.qty,
                          long_occ=pos.long_occ, short_occ=pos.short_occ, entry=pos.net_debit,
                          exit=price, pnl_usd=round(pnl, 2), pnl_pct=round(pos.pnl_pct(price) * 100, 2),
                          peak_pct=round(pos.peak_pct * 100, 2), reason=why,
                          held_min=round((self.now() - pos.opened_at).total_seconds() / 60, 1))
        self.day.register_close(pos.sym, pnl)
        self.open.pop(pos.pos_id, None)
        log.info(f"[engine] CLOSE {pos.sym} {pos.long_occ}/{pos.short_occ} @ {price:.2f} "
                 f"P&L ${pnl:+,.0f} — {why}")

    def flatten_all(self, why: str) -> None:
        for pos in list(self.open.values()):
            mid = self.broker.spread_mid(pos.long_occ, pos.short_occ) or pos.last_mid or pos.net_debit
            self.close(pos, mid, why)

    # ── boot reconciliation ──────────────────────────────────────────────────
    def reconcile(self) -> None:
        """Rebuild open spreads from the ledger; anything at the broker the ledger
        doesn't know about is an orphan and gets flattened (and logged)."""
        ledger_open = self.ledger.open_positions()
        broker_pos = {p["symbol"]: p for p in self.broker.positions() if p["asset_class"] == "us_option"}
        for pos_id, r in ledger_open.items():
            if r.get("dry"):
                continue
            have_long = r["long_occ"] in broker_pos
            if not have_long:
                self.ledger.close(r["sym"], r["setup"], pos_id=pos_id, qty=r["qty"],
                                  long_occ=r["long_occ"], short_occ=r["short_occ"], entry=r["net_debit"],
                                  exit=None, pnl_usd=0.0, pnl_pct=0.0, reason="missing at broker on boot")
                continue
            opened = datetime.fromisoformat(r["ts"])
            st = self.exit_engine.init_position(entry=float(r["net_debit"]))
            self.open[pos_id] = exits.OpenSpread(pos_id, r["sym"], r["direction"], r["setup"],
                                                 r["long_occ"], r["short_occ"], int(r["qty"]),
                                                 float(r["net_debit"]), opened, st)
            self.day.open_by_sym[r["sym"]] = pos_id
        known = {p.long_occ for p in self.open.values()} | {p.short_occ for p in self.open.values()}
        for occ, p in broker_pos.items():
            if occ not in known:
                log.warning(f"[engine] ORPHAN option position {occ} qty {p['qty']} — flattening")
                ok = self.broker.close_position_market(occ)
                self.ledger.write({"kind": "orphan", "sym": occ[:3].rstrip("0123456789"), "setup": "orphan",
                                   "occ": occ, "qty": p["qty"], "closed": ok})
        self.day.save()
        log.info(f"[engine] reconciled: {len(self.open)} open spread(s), {len(broker_pos)} option legs at broker")

    # ── report ───────────────────────────────────────────────────────────────
    def write_report(self) -> None:
        self._reported_for = self.day.date
        try:
            path = report.write(self.day.date, self.ledger, account=self.account,
                                hit_rates=self.hit_rates, day=self.day)
            log.info(f"[engine] EOD report → {path}")
        except Exception as e:
            log.exception(f"[engine] report failed: {e}")
