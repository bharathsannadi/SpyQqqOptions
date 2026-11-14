import pytest
from spyqqq import config
from spyqqq.ledger import Ledger


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger.jsonl")


def test_required_fields_enforced(ledger):
    with pytest.raises(ValueError):
        ledger.write({"kind": "fill", "sym": "SPY"})             # no setup
    with pytest.raises(ValueError):
        ledger.write({"kind": "nope", "sym": "SPY", "setup": "x"})


def test_mode_is_always_written(ledger):
    r = ledger.decision("SPY", "trend_pullback", "bull", False, [{"gate": "g", "ok": False, "text": "KB §3 no"}])
    assert r["mode"] in ("dry", "paper") and r["kind"] == "decision"


def test_open_positions_from_fills_and_closes(ledger):
    ledger.fill("SPY", "s", pos_id="a", long_occ="L", short_occ="S", qty=1, net_debit=1.0)
    ledger.fill("QQQ", "s", pos_id="b", long_occ="L2", short_occ="S2", qty=1, net_debit=1.0)
    ledger.close("SPY", "s", pos_id="a", exit=1.2, pnl_usd=20)
    op = ledger.open_positions()
    assert list(op) == ["b"]
    assert len(ledger.today()) == 3


def test_report_counts_one_close_per_position(ledger, monkeypatch):
    from spyqqq import report
    ledger.fill("SPY", "s", pos_id="a", long_occ="L", short_occ="S", qty=1, net_debit=1.0)
    ledger.close("SPY", "s", pos_id="a", exit=1.2, pnl_usd=20, direction="bull")
    ledger.close("SPY", "s", pos_id="a", exit=1.2, pnl_usd=20, direction="bull")   # duplicate row
    day = ledger.today()[0]["ts"][:10]
    s = report.summarize(day, ledger)
    assert len(s["closes"]) == 1 and s["pnl"] == 20
