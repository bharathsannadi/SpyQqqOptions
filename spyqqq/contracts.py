"""contracts.py — pick the two legs of a vertical debit spread.

Long leg: the first strike at/just beyond spot in the trade direction.
Short leg: SPREAD_WIDTH_STEPS further out. Expiry: nearest inside DTE_MIN–DTE_MAX.
Every KB gate the legs must pass is returned in `gates` so the ledger shows
exactly why a candidate was taken or refused.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from . import config, gates
from .broker import Broker, Contract


@dataclass
class SpreadCandidate:
    sym: str
    direction: str
    opt_type: str
    expiry: date
    dte: int
    long: Contract
    short: Contract
    width: float
    net_debit: float          # long mid − short bid (conservative)
    net_mid: float            # long mid − short mid (what we'll quote the limit from)
    half_spreads: float       # sum of half bid-ask on both legs (limit padding basis)
    gates: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(g["ok"] for g in self.gates)

    def as_dict(self) -> dict:
        return {"sym": self.sym, "direction": self.direction, "type": self.opt_type,
                "expiry": self.expiry.isoformat(), "dte": self.dte,
                "long": self.long.occ, "long_strike": self.long.strike,
                "short": self.short.occ, "short_strike": self.short.strike,
                "width": self.width, "net_debit": round(self.net_debit, 2),
                "net_mid": round(self.net_mid, 2), "gates": self.gates}


def _g(name: str, res: tuple[bool, str]) -> dict:
    return {"gate": name, "ok": res[0], "text": res[1]}


def select(broker: Broker, sym: str, direction: str, spot: float,
           today: date) -> Optional[SpreadCandidate]:
    """Best candidate that passes all leg gates, else the closest failing one
    (so the caller can log why). None only if the chain was empty."""
    opt_type = "call" if direction == "bull" else "put"
    exp_lo, exp_hi = today.__class__.fromordinal(today.toordinal() + config.DTE_MIN), \
                     today.__class__.fromordinal(today.toordinal() + config.DTE_MAX)
    chain = broker.chain(sym, opt_type, exp_lo, exp_hi, spot * 0.95, spot * 1.05)
    if not chain:
        return None
    expiries = sorted({c.expiry for c in chain})
    best_fail: Optional[SpreadCandidate] = None
    for expiry in expiries:
        legs = sorted([c for c in chain if c.expiry == expiry], key=lambda c: c.strike)
        strikes = [c.strike for c in legs]
        if len(strikes) < 4:
            continue
        step = min(round(strikes[i + 1] - strikes[i], 2) for i in range(len(strikes) - 1)) or 1.0
        # candidate long strikes: ATM-ish then one step OTM
        if direction == "bull":
            base = math.ceil(spot / step) * step
            long_strikes = [base, base + step]
        else:
            base = math.floor(spot / step) * step
            long_strikes = [base, base - step]
        by_strike = {c.strike: c for c in legs}
        for ls in long_strikes:
            ss = ls + step * config.SPREAD_WIDTH_STEPS if direction == "bull" else ls - step * config.SPREAD_WIDTH_STEPS
            lg, sh = by_strike.get(round(ls, 2)), by_strike.get(round(ss, 2))
            if not lg or not sh:
                continue
            width = abs(sh.strike - lg.strike)
            net_debit = max(lg.mid - sh.bid, 0.01)
            net_mid = max(lg.mid - sh.mid, 0.01)
            half = ((lg.ask - lg.bid) / 2 if lg.ask and lg.bid else 0) + \
                   ((sh.ask - sh.bid) / 2 if sh.ask and sh.bid else 0)
            dte_n = (expiry - today).days
            cand = SpreadCandidate(sym, direction, opt_type, expiry, dte_n, lg, sh, width,
                                   net_debit, net_mid, half)
            cand.gates = [
                _g("dte", gates.dte(dte_n)),
                _g("liquidity_long", gates.liquidity(lg.oi, lg.bid, lg.ask, f"long {lg.occ}")),
                _g("liquidity_short", gates.liquidity(sh.oi, sh.bid, sh.ask, f"short {sh.occ}")),
                _g("spread_cost", gates.spread_cost(lg.bid, lg.ask, sh.bid, sh.ask, net_mid)),
                _g("spread_ratio", gates.spread_ratio(net_debit, width)),
            ]
            if cand.ok:
                return cand
            if best_fail is None or sum(g["ok"] for g in cand.gates) > sum(g["ok"] for g in best_fail.gates):
                best_fail = cand
    return best_fail
