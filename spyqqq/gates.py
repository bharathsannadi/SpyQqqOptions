"""gates.py — every KB rule the bot enforces, as (ok, principle_sentence).

A gate never raises and never returns a bare bool: the sentence is what the
ledger and the dashboard show, so a rejected day explains itself.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from . import config, vol_edge
from .history import HitRate, confidence_ok


def entry_window(now: datetime, open_gap_pct: Optional[float] = None) -> tuple[bool, str]:
    t = now.timetz().replace(tzinfo=None)
    lo, hi = config.ENTRY_WINDOW_START_ET, config.ENTRY_WINDOW_END_ET
    if open_gap_pct is not None and abs(open_gap_pct) >= config.GAP_DELAY_PCT:
        lo = max(lo, config.GAP_DELAY_START_ET)
        if t < lo:
            return False, (f"KB §3 Entry Timing: {open_gap_pct:+.1f}% opening gap — first 60 min is the "
                           f"highest-whipsaw window, entries from {lo:%H:%M} ET today")
    if lo <= t <= hi:
        return True, f"KB §3 Entry Timing: {t:%H:%M} ET inside {lo:%H:%M}–{hi:%H:%M} window"
    return False, (f"KB §3 Entry Timing: {t:%H:%M} ET outside {lo:%H:%M}–{hi:%H:%M} — "
                   f"no entries in the first 30 min or after late morning")


def vol_edge_gate(hv5: Optional[float], hv30: Optional[float],
                  iv30: Optional[float]) -> tuple[bool, str]:
    if not config.VOL_EDGE_REQUIRED:
        return True, "KB §22 vol-edge gate disabled by config"
    ok, why = vol_edge.long_premium_ok(hv5, hv30, iv30, config.MIN_VOL_EDGE_PTS)
    return ok, "KB §22 Variance Risk Premium: " + why


def confidence_gate(hr: Optional[HitRate]) -> tuple[bool, str]:
    return confidence_ok(hr)


def liquidity(oi: Optional[int], bid: float, ask: float, label: str) -> tuple[bool, str]:
    mid = (bid + ask) / 2 if bid and ask else 0.0
    if mid <= 0 or bid <= 0:
        return False, f"KB §9 Liquidity: {label} has no two-sided quote (bid {bid} / ask {ask})"
    ba = (ask - bid) / mid
    if oi is not None and oi < config.OPT_MIN_OI:
        return False, f"KB §9 Liquidity: {label} open interest {oi} < {config.OPT_MIN_OI} minimum"
    if ba > config.OPT_MAX_BID_ASK_PCT:
        return False, (f"KB §9 Liquidity: {label} bid-ask {ba:.1%} of mid > "
                       f"{config.OPT_MAX_BID_ASK_PCT:.0%} — quote too wide to trust")
    return True, f"KB §9 Liquidity: {label} OI {oi if oi is not None else '?'} ≥ {config.OPT_MIN_OI}, two-sided quote {ba:.1%} wide"


def spread_cost(long_bid: float, long_ask: float, short_bid: float, short_ask: float,
                net_mid: float) -> tuple[bool, str]:
    """KB §9 on the instrument we actually trade: the round-trip quote width of
    the spread as a share of its debit. ≤10% round trip ≈ the 5% one-way rule."""
    if net_mid <= 0:
        return False, "KB §9 Liquidity: spread has no net mid"
    cost = ((long_ask - long_bid) + (short_ask - short_bid)) / net_mid
    if cost > config.OPT_MAX_SPREAD_COST_PCT:
        return False, (f"KB §9 Liquidity: spread quote width {cost:.1%} of debit > "
                       f"{config.OPT_MAX_SPREAD_COST_PCT:.0%} — you pay the spread twice")
    return True, (f"KB §9 Liquidity: spread quote width {cost:.1%} of debit ≤ "
                  f"{config.OPT_MAX_SPREAD_COST_PCT:.0%} (≈{cost/2:.1%} one-way)")


def spread_ratio(net_debit: float, width: float) -> tuple[bool, str]:
    if width <= 0 or net_debit <= 0:
        return False, f"KB §5 Spread Pricing: bad inputs (debit {net_debit}, width {width})"
    r = net_debit / width
    if r < config.SPREAD_RATIO_LO:
        return False, (f"KB §5 Spread Pricing: debit {net_debit:.2f} is {r:.0%} of {width:.0f}-wide — "
                       f"< {config.SPREAD_RATIO_LO:.0%}, too far OTM to pay off")
    if r > config.SPREAD_RATIO_HI:
        return False, (f"KB §5 Spread Pricing: debit {net_debit:.2f} is {r:.0%} of {width:.0f}-wide — "
                       f"> {config.SPREAD_RATIO_HI:.0%}, paying too much of the width")
    return True, (f"KB §5 Spread Pricing: debit {net_debit:.2f} = {r:.0%} of {width:.0f}-wide, "
                  f"inside {config.SPREAD_RATIO_LO:.0%}–{config.SPREAD_RATIO_HI:.0%}")


def dte(days: int) -> tuple[bool, str]:
    if config.DTE_MIN <= days <= config.DTE_MAX:
        return True, f"KB §3 DTE: {days} inside {config.DTE_MIN}–{config.DTE_MAX}"
    return False, f"KB §3 DTE: {days} outside {config.DTE_MIN}–{config.DTE_MAX}"


def debate_gate(result: dict) -> tuple[bool, str]:
    if result["status"] == "off":
        return True, "Debate gate: OFF (no ANTHROPIC_API_KEY or disabled)"
    tag = "PROCEED" if result["proceed"] else "SUPPRESS"
    return result["proceed"], (f"Debate gate ({config.DEBATE_MODEL}): {tag} conf {result['confidence']:.0%} — "
                               f"{result['reason']}")


def event_blackout(d: date) -> tuple[bool, str]:
    """No new entries on FOMC decision days / NFP mornings (operator list + first-Friday rule)."""
    ds = d.isoformat()
    if ds in config.EVENT_BLACKOUT_DATES:
        return False, f"KB §17 Event Risk: {ds} is a scheduled macro-event day (FOMC/CPI) — no new entries (Cofnas)"
    if config.NFP_BLACKOUT and d.weekday() == 4 and d.day <= 7:
        return False, f"KB §17 Event Risk: {ds} is the first Friday (NFP 08:30 ET) — no new entries (Cofnas)"
    if config.WITCHING_BLACKOUT and d.weekday() == 4 and 15 <= d.day <= 21 and d.month in (3, 6, 9, 12):
        return False, (f"KB §17 Event Risk: {ds} is triple witching (index futures/options expiry + SPY/QQQ "
                       f"ex-dividend) — no new entries (Stock Trader's Almanac)")
    return True, f"KB §17 Event Risk: {ds} has no scheduled macro release"


def drawdown_ok(net_recent_usd: float) -> tuple[bool, str]:
    """KB §RM: a rolling loss of two daily budgets over ten sessions means the edge is not
    showing up — stop entering and review (Elder's 6%-per-month rule, scaled to the budget)."""
    if net_recent_usd <= -config.ROLLING_LOSS_HALT_USD:
        return False, (f"KB §RM Drawdown: net ${net_recent_usd:,.0f} over the last {config.ROLLING_LOSS_SESSIONS} "
                       f"sessions ≤ −${config.ROLLING_LOSS_HALT_USD:,.0f} — no new entries until reviewed (Elder 6% rule)")
    return True, (f"KB §RM Drawdown: net ${net_recent_usd:,.0f} over the last {config.ROLLING_LOSS_SESSIONS} "
                  f"sessions is inside −${config.ROLLING_LOSS_HALT_USD:,.0f}")


def slippage_ok(recent_slip_bps: list[float]) -> tuple[bool, str]:
    """Fill-quality circuit breaker. 2026-06-04: the day's killer was 225 bps average
    slippage on relaxed fills, not the stops — and the coach blamed the stops."""
    n = len(recent_slip_bps)
    if n < config.SLIPPAGE_WINDOW:
        return True, f"KB §9 Fill Quality: {n}/{config.SLIPPAGE_WINDOW} recent fills measured, no verdict yet"
    avg = sum(recent_slip_bps[-config.SLIPPAGE_WINDOW:]) / config.SLIPPAGE_WINDOW
    if avg >= config.SLIPPAGE_HALT_BPS:
        return False, (f"KB §9 Fill Quality: avg slippage {avg:.0f} bps over last {config.SLIPPAGE_WINDOW} fills "
                       f"≥ {config.SLIPPAGE_HALT_BPS:.0f} — the edge is not surviving fills, halt entries")
    return True, f"KB §9 Fill Quality: avg slippage {avg:.0f} bps over last {config.SLIPPAGE_WINDOW} fills"


def cooldown_ok(recent_stops: int, sym: str, direction: str) -> tuple[bool, str]:
    """Repeat-loser cooldown. Old ledger: ORCL 7 trades/1 win −$1,355, NVDA 10/1 −$543."""
    if recent_stops >= config.COOLDOWN_STOPS:
        return False, (f"KB §7 Repeat Loser: {sym} {direction} stopped {recent_stops}× in the last "
                       f"{config.COOLDOWN_SESSIONS} sessions — cooling down")
    return True, f"KB §7 Repeat Loser: {sym} {direction} {recent_stops} recent stop(s) < {config.COOLDOWN_STOPS}"


def same_direction_ok(open_directions: list[str], direction: str) -> tuple[bool, str]:
    """SPY and QQQ are ~0.9 correlated: two same-way spreads are one doubled bet (KB §5, 2026-06-05)."""
    n = sum(1 for d in open_directions if d == direction)
    if n >= config.MAX_SAME_DIRECTION_OPEN:
        return False, (f"KB §5 Correlation: already {n} open {direction} spread(s) — SPY/QQQ move together, "
                       f"max {config.MAX_SAME_DIRECTION_OPEN} same-direction")
    return True, f"KB §5 Correlation: {n} open {direction} spread(s) < {config.MAX_SAME_DIRECTION_OPEN}"


def summarize(gates: list[dict]) -> tuple[bool, list[str]]:
    failed = [g["text"] for g in gates if not g["ok"]]
    return (not failed), failed
