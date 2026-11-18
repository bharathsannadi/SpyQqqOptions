"""Operational guards in cli.py: one engine per account, ET log timestamps with pid."""
import logging
import subprocess
import sys
import textwrap

import pytest

from spyqqq import cli, config


def test_second_engine_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import fcntl, time
        f = open({str(tmp_path / 'engine.lock')!r}, 'a+'); fcntl.flock(f, fcntl.LOCK_EX); f.write('4242'); f.flush()
        print('held', flush=True); time.sleep(30)""")], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(SystemExit) as e:
            cli._single_instance("engine")
        assert e.value.code == 3
    finally:
        holder.kill(); holder.wait()
    cli._single_instance("engine")                     # lock released with the holder → we get it
    assert (tmp_path / "engine.lock").read_text().strip().isdigit()


def test_log_lines_are_eastern_with_pid():
    fmt = cli._ETFormatter("%(asctime)s pid=%(process)d %(message)s")
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", None, None)
    rec.created = 1_794_927_600.0                      # 2026-11-17 15:00:00 UTC
    line = fmt.format(rec)
    assert line.startswith("2026-11-17 10:00:00-0500 pid=") and line.endswith("hello")


def test_dry_run_shares_the_live_lock(tmp_path, monkeypatch):
    """A dry engine writes the same day_state/status/heartbeat — it must not run beside live."""
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(cli, "_logging", lambda name: calls.append(("log", name)))
    monkeypatch.setattr(cli, "_single_instance", lambda name: calls.append(("lock", name)) or (_ for _ in ()).throw(SystemExit(3)))
    monkeypatch.setattr(cli, "_keep_awake", lambda: calls.append(("awake",)))
    class A: dry = True
    with pytest.raises(SystemExit):
        cli.cmd_run(A())
    assert ("lock", "engine") in calls and ("log", "engine-dry") in calls and ("awake",) not in calls


def test_report_measures_host_sleep_inside_the_session():
    from spyqqq import report
    log = "\n".join([
        "2026-11-16 08:14:02 -0600 Sleep               \tEntering Sleep state due to 'Idle Sleep'",   # 09:14 ET — before open
        "2026-11-16 08:14:03 -0600 Wake Requests       \t[process=mDNSResponder ...]",                 # not a wake
        "2026-11-16 08:44:32 -0600 DarkWake            \tDarkWake from Deep Idle",                    # 09:44 ET: 14 min in session
        "2026-11-16 08:49:44 -0600 Sleep               \tEntering Sleep state",
        "2026-11-16 09:15:31 -0600 Wake                \tWake from Deep Idle",                        # +25.8 min
        "2026-11-16 14:50:00 -0600 Sleep               \tEntering Sleep state",                       # 15:50 ET → close: +10
    ])
    mins, n = report.host_sleep("2026-11-16", log)
    assert n == 3 and abs(mins - (14.53 + 25.78 + 10)) < 0.2
    assert report.host_sleep("2026-11-16", "") == (0.0, 0)


def _catchup_self(tmp_path, monkeypatch, bar_day, day_date="2026-11-16"):
    from types import SimpleNamespace
    from datetime import datetime
    import pytz
    from spyqqq import config
    monkeypatch.setattr(config, "REPORT_DIR", tmp_path)
    ET = pytz.timezone("America/New_York")
    calls = {"bars": 0, "report": 0}
    def bars(sym, days=3):
        calls["bars"] += 1
        return [{"ts": ET.localize(datetime.strptime(bar_day, "%Y-%m-%d")).astimezone(pytz.utc)}]
    s = SimpleNamespace(_reported_for="", day=SimpleNamespace(date=day_date),
                        broker=SimpleNamespace(daily_bars=bars))
    s.write_report = lambda: calls.__setitem__("report", calls["report"] + 1)
    return s, calls, ET


def test_missed_eod_report_is_caught_up_once_after_close(tmp_path, monkeypatch):
    from datetime import datetime
    from spyqqq.engine import Engine
    s, calls, ET = _catchup_self(tmp_path, monkeypatch, "2026-11-16")
    Engine.catch_up_report(s, ET.localize(datetime(2026, 11, 16, 15, 59)))   # before cut-off: nothing
    assert calls == {"bars": 0, "report": 0}
    for _ in range(3):
        Engine.catch_up_report(s, ET.localize(datetime(2026, 11, 16, 16, 30)))
    assert calls == {"bars": 1, "report": 1}


def test_no_catch_up_report_on_holiday_or_when_written(tmp_path, monkeypatch):
    from datetime import datetime
    from spyqqq.engine import Engine
    s, calls, ET = _catchup_self(tmp_path, monkeypatch, "2026-11-13")           # last bar = Friday
    Engine.catch_up_report(s, ET.localize(datetime(2026, 11, 16, 17, 0)))
    assert calls == {"bars": 1, "report": 0}
    s, calls, ET = _catchup_self(tmp_path, monkeypatch, "2026-11-16")
    (tmp_path / "2026-11-16.md").write_text("x")                               # the 15:45 tick already wrote it
    Engine.catch_up_report(s, ET.localize(datetime(2026, 11, 16, 17, 0)))
    assert calls == {"bars": 0, "report": 0}


def test_llm_failure_reason_reaches_report_and_debate(monkeypatch):
    from types import SimpleNamespace
    from spyqqq import llm, debate, config
    class Boom:
        class beta:
            class messages:
                @staticmethod
                def create(**k):
                    raise RuntimeError("Error code: 400 - Your credit balance is too low to access the Anthropic API.")
    monkeypatch.setattr(llm, "_client", Boom())
    monkeypatch.setattr(config, "DEBATE_ENABLED", True)
    assert llm.ask("s", "u", "m") is None
    assert llm.last_error == "Anthropic API credit balance too low"
    sig_ = SimpleNamespace(sym="SPY", direction="bull", setup="vwap_momentum", strength=60, price=500.0,
                           reasons=[], indicators={})
    monkeypatch.setattr(debate, "_setup_text", lambda s, e: "x")
    out = debate.run(sig_)
    assert out["status"] == "failed_closed" and "credit balance" in out["reason"]


def test_llm_probe_sets_and_clears_error(monkeypatch):
    from spyqqq import llm
    state = {"fail": True}
    class C:
        class messages:
            @staticmethod
            def create(**k):
                if state["fail"]:
                    raise RuntimeError("Your credit balance is too low to access the Anthropic API.")
    monkeypatch.setattr(llm, "_client", C())
    assert llm.probe("m") == "Anthropic API credit balance too low"
    state["fail"] = False
    assert llm.probe("m") == "" and llm.last_error == ""


def test_rollover_probes_claude_only_on_a_new_session():
    """2026-11-17: probe_llm sat outside the new-day `if` → a billable Claude call every tick."""
    from types import SimpleNamespace
    from datetime import datetime
    import pytz
    from spyqqq.engine import Engine
    n = {"probe": 0}
    s = SimpleNamespace(day=SimpleNamespace(date="2026-11-17"),
                        probe_llm=lambda: n.__setitem__("probe", n["probe"] + 1),
                        refresh_history=lambda: None)
    now = pytz.timezone("America/New_York").localize(datetime(2026, 11, 17, 16, 30))
    for _ in range(5):
        Engine.rollover(s, now)
    assert n["probe"] == 0
