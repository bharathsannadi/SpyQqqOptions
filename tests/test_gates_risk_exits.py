"""KB gates, the day's money rules, and the exit rules."""
from datetime import datetime, timedelta
import pytz, pytest

from spyqqq import config, exits, gates, risk
from spyqqq.history import HitRate

ET = pytz.timezone("America/New_York")


def _at(h, m):
    return ET.localize(datetime(2026, 11, 13, h, m))


# ── gates ────────────────────────────────────────────────────────────────────

def test_entry_window_text_names_the_section():
    ok, why = gates.entry_window(_at(10, 15)); assert ok and "§3" in why
    ok, why = gates.entry_window(_at(9, 45)); assert not ok and "§3" in why
    assert not gates.entry_window(_at(13, 0))[0]


def test_vol_edge_gate_fails_closed_on_missing_iv():
    ok, why = gates.vol_edge_gate(12.0, 14.0, None)
    assert not ok and "§22" in why and "incomplete" in why
    assert gates.vol_edge_gate(26.0, 18.0, 15.0)[0]


def test_liquidity_gate():
    assert gates.liquidity(500, 1.00, 1.04, "x")[0]
    ok, why = gates.liquidity(50, 1.00, 1.04, "x"); assert not ok and "open interest" in why
    ok, why = gates.liquidity(500, 1.00, 1.40, "x"); assert not ok and "bid-ask" in why
    assert gates.spread_cost(1.05, 1.07, 0.34, 0.35, 0.715)[0]
    ok, why = gates.spread_cost(1.00, 1.10, 0.30, 0.36, 0.72); assert not ok and "§9" in why
    assert not gates.liquidity(500, 0.0, 1.0, "x")[0]


def test_spread_ratio_band():
    assert gates.spread_ratio(0.70, 2.0)[0]          # 35%
    assert not gates.spread_ratio(0.30, 2.0)[0]      # 15% too cheap/OTM
    assert not gates.spread_ratio(1.10, 2.0)[0]      # 55% paying too much


def test_debate_gate_off_and_suppress():
    assert gates.debate_gate({"status": "off", "proceed": True, "confidence": 1.0, "reason": ""})[0]
    ok, why = gates.debate_gate({"status": "ok", "proceed": False, "confidence": 0.4, "reason": "theta"})
    assert not ok and "SUPPRESS" in why


def test_gap_day_delays_entry_window():
    ok, why = gates.entry_window(_at(10, 15), open_gap_pct=1.4)
    assert not ok and "gap" in why
    assert gates.entry_window(_at(10, 35), open_gap_pct=1.4)[0]
    assert gates.entry_window(_at(10, 15), open_gap_pct=0.3)[0]


def test_event_blackout_fomc_and_nfp():
    from datetime import date
    ok, why = gates.event_blackout(date(2026, 12, 9)); assert not ok and "FOMC" in why
    ok, why = gates.event_blackout(date(2026, 12, 4)); assert not ok and "NFP" in why       # first Friday
    assert gates.event_blackout(date(2026, 12, 10))[0]


def test_slippage_breaker():
    assert gates.slippage_ok([50, 80])[0]                      # not enough fills yet
    ok, why = gates.slippage_ok([400, 350, 300, 280, 320]); assert not ok and "halt" in why
    assert gates.slippage_ok([40, 60, 30, 80, 50])[0]


def test_cooldown_and_same_direction():
    ok, why = gates.cooldown_ok(2, "SPY", "bull"); assert not ok and "Repeat Loser" in why
    assert gates.cooldown_ok(1, "SPY", "bull")[0]
    ok, why = gates.same_direction_ok(["bull"], "bull"); assert not ok and "Correlation" in why
    assert gates.same_direction_ok(["bull"], "bear")[0]
    assert gates.same_direction_ok([], "bull")[0]


# ── day risk ─────────────────────────────────────────────────────────────────

@pytest.fixture
def day(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DAY_STATE_PATH", tmp_path / "day.json")
    monkeypatch.setattr(config, "HALT_PATH", tmp_path / "HALT")
    return risk.DayState()


def test_per_trade_cap_and_daily_budget(day):
    assert day.can_enter("SPY", 250)[0]
    ok, why = day.can_enter("SPY", 350); assert not ok and "§4" in why
    day.register_entry("SPY", "p1", 300)
    ok, why = day.can_enter("QQQ", 300); assert ok
    day.register_entry("QQQ", "p2", 300)
    ok, why = day.can_enter("SPY", 10); assert not ok      # 2 trades/day reached


def test_daily_budget_exhaustion_before_trade_count(day, monkeypatch):
    monkeypatch.setattr(config, "MAX_TRADES_PER_DAY", 5)
    monkeypatch.setattr(config, "MAX_OPEN", 5)
    day.register_entry("SPY", "p1", 300); day.register_close("SPY", 10)
    day.register_entry("QQQ", "p2", 300); day.register_close("QQQ", 10)
    ok, why = day.can_enter("SPY", 100); assert not ok and "daily budget" in why


def test_loss_halt_blocks_entries_not_exits(day):
    day.register_close("SPY", -320)
    ok, why = day.can_enter("QQQ", 100)
    assert not ok and "daily loss halt" in why


def test_operator_halt_file(day):
    config.HALT_PATH.write_text("x")
    assert not day.can_enter("SPY", 100)[0]


def test_contracts_for_respects_both_caps(day):
    assert day.contracts_for(0.80) == 3        # $80 each, $300 cap → 3
    day.register_entry("SPY", "p", 500)
    assert day.contracts_for(0.80) == 1        # $100 of budget left


def test_open_directions_tracked(day):
    day.register_entry("SPY", "p1", 200, "bull")
    assert day.open_directions() == ["bull"]
    day.register_close("SPY", 5)
    assert day.open_directions() == []


def test_day_state_persists_and_resets_on_new_date(day, tmp_path):
    day.register_entry("SPY", "p1", 200)
    again = risk.DayState.load()
    assert again.trades == 1 and again.debit_spent_usd == 200
    again.date = "2000-01-01"; again.save()
    assert risk.DayState.load().trades == 0


# ── exits ────────────────────────────────────────────────────────────────────

def _pos(entry=1.00, opened=_at(10, 30)):
    eng = exits.new_engine()
    return exits.OpenSpread("p", "SPY", "bull", "trend_pullback", "L", "S", 2, entry, opened,
                            eng.init_position(entry)), eng


def test_take_profit_and_stop_on_net_debit():
    pos, eng = _pos()
    assert exits.check(pos, 1.21, _at(11, 0), eng)[0] == "close"
    pos, eng = _pos()
    a, why = exits.check(pos, 0.79, _at(11, 0), eng)
    assert a == "close" and "initial stop" in why


def test_hold_inside_band_and_floor_ratchets():
    pos, eng = _pos()
    assert exits.check(pos, 1.03, _at(11, 0), eng)[0] == "hold"
    exits.check(pos, 1.12, _at(11, 5), eng)          # +12% → breakeven floor set
    a, why = exits.check(pos, 0.98, _at(11, 10), eng)
    assert a == "close" and "breakeven" in why


def test_stall_and_flatten_time():
    pos, eng = _pos(opened=_at(10, 0))
    a, why = exits.check(pos, 1.02, _at(11, 35), eng)
    assert a == "close" and "stall" in why
    pos, eng = _pos(opened=_at(14, 0))
    a, why = exits.check(pos, 1.02, _at(15, 46), eng)
    assert a == "close" and "flatten" in why
