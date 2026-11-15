"""Contract selection against a fake chain — no network."""
from datetime import date
from spyqqq import contracts
from spyqqq.broker import Contract


class FakeBroker:
    def __init__(self, chain): self._chain = chain
    def chain(self, *a, **k): return self._chain


def _c(strike, bid, ask, exp=date(2026, 11, 27), oi=1500):
    return Contract(occ=f"SPY{exp:%y%m%d}C{int(strike*1000):08d}", strike=strike, expiry=exp,
                    opt_type="call", oi=oi, bid=bid, ask=ask)


def test_picks_otm_long_and_five_wide_short_passing_all_gates():
    spot, today = 100.4, date(2026, 11, 13)
    # SPY-like ladder: strikes are ~0.15% apart so premium decays slowly outward
    # (measured 2022-26: long 2 OTM on a 5-wide ≈ 0.43 of width)
    px = {100: 3.30, 101: 3.00, 102: 2.75, 103: 2.50, 104: 2.20, 105: 1.90, 106: 1.62,
          107: 1.35, 108: 1.10, 109: 0.90}
    chain = [_c(k, v, round(v + 0.02, 2)) for k, v in px.items()]
    cand = contracts.select(FakeBroker(chain), "SPY", "bull", spot, today)
    assert cand is not None and cand.ok, [g for g in cand.gates if not g["ok"]]
    # BK-1 (Natenberg): the long nearest the money that still satisfies §5 wins — 102/107 at 28% of width
    assert cand.long.strike == 102 and cand.short.strike == 107 and cand.width == 5
    assert 0.25 * 5 <= cand.net_debit <= 0.45 * 5


def test_returns_closest_failure_with_gate_text_when_illiquid():
    spot, today = 100.4, date(2026, 11, 13)
    chain = [_c(k, v, v + 0.3, oi=20) for k, v in {103: 1.28, 104: 0.97, 105: 0.72, 106: 0.52, 107: 0.37, 108: 0.26, 109: 0.18}.items()]
    cand = contracts.select(FakeBroker(chain), "SPY", "bull", spot, today)
    assert cand is not None and not cand.ok
    assert any("§9" in g["text"] for g in cand.gates if not g["ok"])


def test_empty_chain_is_none():
    assert contracts.select(FakeBroker([]), "SPY", "bull", 100, date(2026, 11, 13)) is None
