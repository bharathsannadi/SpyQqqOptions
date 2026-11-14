"""gates.py — every KB rule the bot enforces, as (ok, principle_sentence).

A gate never raises and never returns a bare bool: the sentence is what the
ledger and the dashboard show, so a rejected day explains itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from . import config, vol_edge
from .history import HitRate, confidence_ok


def entry_window(now: datetime) -> tuple[bool, str]:
    t = now.timetz().replace(tzinfo=None)
    lo, hi = config.ENTRY_WINDOW_START_ET, config.ENTRY_WINDOW_END_ET
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


def summarize(gates: list[dict]) -> tuple[bool, list[str]]:
    failed = [g["text"] for g in gates if not g["ok"]]
    return (not failed), failed
