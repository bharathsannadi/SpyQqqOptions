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


def test_picks_atm_long_and_two_wide_short_passing_all_gates():
    spot, today = 100.4, date(2026, 11, 13)
    chain = [_c(99, 2.30, 2.33), _c(100, 1.62, 1.64), _c(101, 1.05, 1.07), _c(102, 0.62, 0.64),
             _c(103, 0.34, 0.35), _c(104, 0.17, 0.18)]
    cand = contracts.select(FakeBroker(chain), "SPY", "bull", spot, today)
    assert cand is not None and cand.ok
    assert cand.long.strike == 101 and cand.short.strike == 103 and cand.width == 2
    assert 0.25 * 2 <= cand.net_debit <= 0.45 * 2


def test_returns_closest_failure_with_gate_text_when_illiquid():
    spot, today = 100.4, date(2026, 11, 13)
    chain = [_c(101, 1.05, 1.40, oi=20), _c(102, 0.62, 0.66, oi=20), _c(103, 0.30, 0.60, oi=20), _c(104, 0.1, 0.2, oi=20)]
    cand = contracts.select(FakeBroker(chain), "SPY", "bull", spot, today)
    assert cand is not None and not cand.ok
    assert any("§9" in g["text"] for g in cand.gates if not g["ok"])


def test_empty_chain_is_none():
    assert contracts.select(FakeBroker([]), "SPY", "bull", 100, date(2026, 11, 13)) is None
