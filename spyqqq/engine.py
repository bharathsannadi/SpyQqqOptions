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
from .risk import DayState, recent_slippage_bps, recent_stops, recent_net_usd
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
        self.hit_rates: dict[str, dict[str, dict[str, history.HitRate]]] = {}   # sym → setup → dir
        self._last_reconcile = 0.0
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
        self.probe_llm()
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
        if c is None:
            c = self._clock_fallback()
        else:
            self._last_good_clock = c
        self._clock_cache = (_time.time(), c)
        return c

    def _clock_fallback(self) -> Optional[dict]:
        """A failed clock call used to read as "market closed" — skipping exits and
        flatten for open spreads. Infer the session from the last good clock instead."""
        c = getattr(self, "_last_good_clock", None)
        if not c:
            return None
        now = datetime.now(ET)
        nxt_open, nxt_close = c.get("next_open"), c.get("next_close")
        try:
            if c["is_open"]:
                is_open = nxt_close is None or now < nxt_close
            else:
                is_open = nxt_open is not None and nxt_open <= now < (nxt_close or nxt_open)
        except TypeError:
            return None
        log.warning(f"[engine] clock unavailable — using last good clock (open={is_open})")
        return dict(c, is_open=is_open)

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

        if market_open and t - self._last_reconcile >= config.RECONCILE_EVERY_SEC:
            self._last_reconcile = t
            self.reconcile_light()

        if market_open and exits.is_flatten_time(now):
            self.flatten_all("EOD flatten")
            if self._reported_for != self.day.date and now.time() >= config.FLATTEN_AT_ET:
                self.write_report()

        if not market_open:
            self.catch_up_report(now)

        if market_open and t - self._last_scan >= config.SCAN_INTERVAL_SEC:
            self._last_scan = t
            in_window = gates.entry_window(now)[0]
            for sym in config.UNDERLYINGS:
                # outside the window the scan is observe-only: the dashboard keeps showing
                # price / trend / signal, but nothing is gated, logged or ordered
                self.scan(sym, now, observe=not in_window)

        # while Claude is failing, re-check every 30 min so a credit top-up clears the
        # dashboard pill (and re-opens the debate gate) without an engine restart
        from . import llm
        if llm.last_error and t - getattr(self, "_last_probe", 0) >= 1800:
            self.probe_llm()

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
            self.last_signal = {}
            self.refresh_history()
            self.probe_llm()

    # ── history / confidence ─────────────────────────────────────────────────
    def probe_llm(self) -> None:
        self._last_probe = _time.time()
        from . import llm
        err = llm.probe(config.DEBATE_MODEL if config.DEBATE_ENABLED else config.REVIEW_MODEL)
        use = "debate gate + EOD review" if config.DEBATE_ENABLED else "EOD review (debate gate is OFF)"
        log.info(f"[engine] Claude for {use}: {'reachable' if not err else 'NOT reachable — ' + err}")

    def refresh_history(self) -> None:
        for sym in config.UNDERLYINGS:
            try:
                intraday = self.md.history(sym)
                daily = self.md.daily_by_date(sym, sorted(intraday))
                self.hit_rates[sym] = history.replay_all(sym, daily, intraday)
                for st, by_dir in self.hit_rates[sym].items():
                    for d, hr in by_dir.items():
                        log.info(f"[history] {sym} {st} {d}: {hr.label()}")
            except Exception as e:
                log.warning(f"[history] {sym}: {e}")

    # ── scan ─────────────────────────────────────────────────────────────────
    def scan(self, sym: str, now: datetime, observe: bool = False) -> None:
        daily_closes = self.md.daily_closes(sym)
        trend = sig.daily_trend(daily_closes)
        bars = self.md.today_bars(sym)
        warmup = self.md.prev_session_bars(sym)
        gap = None
        if bars and len(daily_closes) >= 2:
            prev_close = daily_closes[-1]           # daily_closes = completed sessions only
            gap = (bars[0].open - prev_close) / prev_close * 100.0
        snap = {"ts": now.isoformat(timespec="seconds"), "trend": trend, "bars": len(bars),
                "price": bars[-1].close if bars else None, "gap_pct": None if gap is None else round(gap, 2),
                "signal": None, "gates": [], "confidence": None, "candidate": None, "debate": None,
                "accepted": False, "observe": observe}
        if bars:
            closes = [b.close for b in list(warmup) + list(bars)]   # warm up across the open
            e = sig.ema_series(closes, config.PULLBACK_EMA)[-1]
            vw = sig.vwap_series(bars)[-1]
            r = sig.rsi(closes, config.RSI_LEN)
            snap["indicators"] = {"ema9": round(e, 2) if e else None,
                                  "rsi14": round(r, 1) if r is not None else None,
                                  "vwap": round(vw, 2)}
        hr_all = self.hit_rates.get(sym, {})
        snap["history"] = {st: {d: h.as_dict() for d, h in by_dir.items()} for st, by_dir in hr_all.items()}
        signals = sig.evaluate_all(sym, trend, bars, warmup)
        if not signals:
            self.last_signal[sym] = snap
            return
        # several setups may fire: take the one with the best historical lower bound
        def _conf(x):
            h = hr_all.get(x.setup, {}).get(x.direction)
            return (h.lower95 or 0.0) if h else 0.0
        s = max(signals, key=_conf)
        snap["signal"] = {"direction": s.direction, "setup": s.setup, "strength": s.strength,
                          "price": s.price, "indicators": s.indicators,
                          "also": [f"{x.setup}:{x.direction}" for x in signals if x is not s]}
        G = lambda name, res: {"gate": name, "ok": res[0], "text": res[1]}
        if observe:
            hr = hr_all.get(s.setup, {}).get(s.direction)
            snap["confidence"] = hr.as_dict() if hr else None
            snap["gates"] = [G("entry_window", gates.entry_window(now, gap))]
            self.last_signal[sym] = snap
            return
        g: list[dict] = [G("entry_window", gates.entry_window(now, gap)),
                         G("signal_fresh", gates.signal_fresh(now, s.ts)),
                         G("event_blackout", gates.event_blackout(now.date()))]
        hr = hr_all.get(s.setup, {}).get(s.direction)
        snap["confidence"] = hr.as_dict() if hr else None
        g.append(G("confidence", gates.confidence_gate(hr)))
        g.append(G("cooldown", gates.cooldown_ok(
            recent_stops(self.ledger, sym, s.direction, config.COOLDOWN_SESSIONS), sym, s.direction)))
        g.append(G("same_direction", gates.same_direction_ok(self.day.open_directions(), s.direction)))
        g.append(G("fill_quality", gates.slippage_ok(recent_slippage_bps(self.ledger, config.SLIPPAGE_WINDOW))))
        g.append(G("drawdown", gates.drawdown_ok(recent_net_usd(self.ledger, config.ROLLING_LOSS_SESSIONS))))
        s.hv5, s.hv30 = self.md.hv(sym)
        s.iv30 = self.md.iv30(sym, s.price)
        self.md.record_iv(sym, s.iv30)
        s.ivr = self.md.iv_rank(sym, s.iv30)
        g.append(G("vol_edge", gates.vol_edge_gate(s.hv5, s.hv30, s.iv30)))
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
                                 "qty": qty, "max_loss_usd": round(cand.net_debit * 100 * qty, 2),
                                 "historical_confidence": hr.label() if hr else "none"})
            snap["debate"] = {k: res[k] for k in ("proceed", "confidence", "reason", "status")}
            g.append(G("debate", gates.debate_gate(res)))
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
        self.day.register_entry(s.sym, pos_id, price * 100 * qty, s.direction)
        log.info(f"[engine] OPEN {s.sym} {s.direction} {cand.long.occ}/{cand.short.occ} x{qty} @ {price:.2f}")

    def monitor(self, now: datetime) -> None:
        for pos_id, pos in list(self.open.items()):
            mid = self.broker.spread_mid(pos.long_occ, pos.short_occ)
            if mid is None:
                last_q = pos.last_quote_at or pos.opened_at
                stale = (now - last_q).total_seconds()
                if stale >= config.STALE_QUOTE_CLOSE_SEC:
                    fallback = (pos.last_mid or pos.net_debit) * 0.95
                    self.ledger.engine("stale_quote", pos_id=pos_id, sym=pos.sym, stale_sec=int(stale))
                    self.close(pos, fallback, f"no spread quote for {stale/60:.0f} min — an unmanaged "
                                              f"position is not acceptable (2026-09 SMCI lesson)")
                continue
            pos.last_quote_at = now
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
        fees = config.FEE_PER_SPREAD_RT * pos.qty
        pnl = (price - pos.net_debit) * 100 * pos.qty - fees
        self.ledger.close(pos.sym, pos.setup, pos_id=pos.pos_id, direction=pos.direction, qty=pos.qty,
                          long_occ=pos.long_occ, short_occ=pos.short_occ, entry=pos.net_debit,
                          exit=price, pnl_usd=round(pnl, 2), fees_usd=round(fees, 2),
                          pnl_pct=round(pos.pnl_pct(price) * 100, 2),
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

    def reconcile_light(self) -> None:
        """Mid-session truth check against the broker (the old app was 'confidently
        wrong every time' it trusted its own store — 2026-09-11)."""
        broker_pos = {p["symbol"]: p for p in self.broker.positions() if p["asset_class"] == "us_option"}
        if not broker_pos and self.open and not self.broker.alive():
            return                                   # a read failure is not "flat"
        for pos_id, pos in list(self.open.items()):
            if pos.long_occ not in broker_pos:
                log.warning(f"[engine] {pos.sym} spread {pos_id} vanished at broker — marking closed")
                price = pos.last_mid or pos.net_debit
                self.ledger.close(pos.sym, pos.setup, pos_id=pos_id, direction=pos.direction, qty=pos.qty,
                                  long_occ=pos.long_occ, short_occ=pos.short_occ, entry=pos.net_debit,
                                  exit=price, pnl_usd=round((price - pos.net_debit) * 100 * pos.qty, 2),
                                  pnl_pct=round(pos.pnl_pct(price) * 100, 2), peak_pct=round(pos.peak_pct * 100, 2),
                                  reason="vanished at broker (reconcile)", held_min=0)
                self.day.register_close(pos.sym, (price - pos.net_debit) * 100 * pos.qty)
                self.open.pop(pos_id, None)
        known = {p.long_occ for p in self.open.values()} | {p.short_occ for p in self.open.values()}
        for occ, p in broker_pos.items():
            if occ not in known:
                log.warning(f"[engine] ORPHAN leg {occ} qty {p['qty']} mid-session — flattening")
                ok = self.broker.close_position_market(occ)
                self.ledger.write({"kind": "orphan", "sym": occ[:3].rstrip("0123456789"), "setup": "orphan",
                                   "occ": occ, "qty": p["qty"], "closed": ok, "when": "mid-session"})

    # ── report ───────────────────────────────────────────────────────────────
    def catch_up_report(self, now: datetime) -> None:
        """The EOD report used to be written only by a tick inside 15:45–16:00 — an engine
        asleep or restarting then (2026-11-16) left the day with no report at all. After the
        close, write it once if today was a session (SPY has a daily bar dated today)."""
        today = now.strftime("%Y-%m-%d")
        if self._reported_for == today or now.time() < config.REPORT_CATCHUP_AFTER_ET:
            return
        if (config.REPORT_DIR / f"{today}.md").exists():
            self._reported_for = today; return
        bars = self.broker.daily_bars("SPY", days=3)
        if not bars:
            return                       # data down — retry next tick (cheap: off-hours cadence 15s)
        self._reported_for = today       # decided either way: no repeat calls
        if bars[-1]["ts"].astimezone(ET).strftime("%Y-%m-%d") == today and self.day.date == today:
            log.info("[engine] no EOD report written in the 15:45–16:00 window — writing it now")
            self.write_report()

    def write_report(self) -> None:
        self._reported_for = self.day.date
        try:
            path = report.write(self.day.date, self.ledger, account=self.account,
                                hit_rates=self.hit_rates, day=self.day)
            log.info(f"[engine] EOD report → {path}")
        except Exception as e:
            log.exception(f"[engine] report failed: {e}")
