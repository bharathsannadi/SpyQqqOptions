# Runbook

## First Monday (2026-11-16) checklist

1. **Debate gate is OFF** (`DEBATE_ENABLED = False`, set 2026-11-15) because the Anthropic
   key has no credits. Entries are gated by the KB rules + historical confidence only, and the
   EOD report prints "review unavailable" instead of the Claude paragraph. When credits are
   added (console.anthropic.com → Plans & Billing): set `DEBATE_ENABLED = True`, restart.
2. At 09:35 confirm the four legacy sells (CVNA/INTC/NVDA/PLTR) filled: the old paper
   positions should be gone from the Alpaca paper account.
3. `make status` → `connected=True`, `status_age` small, history lines for both setups.
4. Dashboard `http://127.0.0.1:5050`: phase card counts down to the 10:05 scan; SPY/QQQ cards
   show the signal banner, confidence bars and the gate pipeline.
5. After 15:45 read `reports/2026-11-16.md`.

## Yearly / monthly maintenance

- `EVENT_BLACKOUT_DATES` in `spyqqq/config.py`: FOMC decision days (seeded for 2026 —
  verify against federalreserve.gov each January) and CPI release days (add from the BLS
  calendar). NFP is automatic (first Friday).

## Daily

| When (ET) | What happens | What you do |
|---|---|---|
| boot / 09:30 | engine connects, reconciles, recomputes confidence tables | nothing — check `http://127.0.0.1:5050` shows green pills and the countdown |
| 10:05–11:30 | scans every 60s; ≤3 spreads (2 open), ≤$1,500 debit | watch the SPY/QQQ cards: signal banner, confidence bars, gate chips |
| 11:30–15:45 | observe-only scans keep the cards live; exits run | nothing |
| until 15:45 | monitors every 10s; ±20%, stall, ratchet | nothing |
| 15:45 | flattens; writes `reports/YYYY-MM-DD.md` with the Claude review | **read the report**; change at most one constant in `spyqqq/config.py`; restart |

## Commands

```bash
make status                 # one-screen state (from state/status.json)
make report                 # regenerate + print today's report
venv/bin/python -m spyqqq history   # confidence table
venv/bin/python -m spyqqq halt      # no new entries; exits keep running
venv/bin/python -m spyqqq resume
make test                   # 84 hermetic tests, < 1s
make dry                    # run the engine with DRY_RUN (decides, logs, never orders)
```

## Install / restart under launchd

```bash
make install        # bootstraps com.spyqqq, com.spyqqq.web, com.spyqqq.watchdog
make uninstall
launchctl kickstart -k gui/$(id -u)/com.spyqqq     # restart after a config change
launchctl list | grep spyqqq
```

Logs: `logs/engine.log` (rotating), `logs/launchd.*.log`, `logs/web.log`, `logs/watchdog.log`.

## If…

- **`status_age` is red / "DISCONNECTED"** — `tail -50 logs/engine.log`. Connect retries
  forever; "connect attempt N failed" lines are expected on a DNS blip and stop on their own.
- **The watchdog killed it** — `logs/watchdog.log` says why (heartbeat age). launchd restarts;
  boot reconciliation rebuilds open spreads from the ledger.
- **An orphan leg appears** — the engine flattens it at market on boot and writes an
  `orphan` ledger row. Investigate the broker UI; it should not happen.
- **Zero signals for days** — the report's "Why trades were refused" section lists the
  first failing gate per decision. If there are no decisions at all, the daily trend was
  flat or nothing pulled back inside the window.
- **Change a limit** — edit `spyqqq/config.py` only, restart with `kickstart -k`. Never
  edit a number anywhere else; the tests pin the gate texts.
