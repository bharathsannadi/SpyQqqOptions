# SpyQqqOptions

Paper-only bot that buys SPY / QQQ vertical debit spreads inside a 90-minute morning
window, under a hard daily budget, flattens at 15:45 ET, and writes an end-of-day
review you read before changing anything. Successor to AlpacaTrader
(see [`docs/CURRENT_SYSTEM.md`](docs/CURRENT_SYSTEM.md) for why).

```
Rules (spyqqq/config.py)        Daily budget $600 · $300/trade · 2 trades · 2 open
                                ±20% on net debit · 90-min stall · flatten 15:45 ET
                                entries 10:00–11:30 ET · 7–21 DTE · 5-wide, long 2–3 strikes OTM
Setups (both measured)          trend_pullback · vwap_momentum (ported; SPY bull 56%, CI 51–62%, n=287)
Gates (each logs its KB §)      §3 window+gap · §17 event blackout · §12 historical confidence
                                §7 cooldown · §5 correlation · §9 fill quality · §22 vol edge
                                §9 liquidity · §5 spread pricing · §4 day risk · debate (OFF until API credits)
Model                           claude-opus-5-5 (debate + EOD review; both skipped gracefully without credits)
```

## Quick start

```bash
cp .env.example .env            # paper keys + ANTHROPIC_API_KEY; chmod 600 .env
/usr/local/Cellar/python@3.11/3.11.15_1/bin/python3.11 -m venv venv
venv/bin/pip install -r requirements-dev.txt
make test                       # 84 tests
make dry                        # engine with DRY_RUN — decides and logs, never orders
make web                        # http://127.0.0.1:5050
make install                    # launchd: engine + web + watchdog
```

## Layout

```
spyqqq/
  config.py     every tunable               engine.py    the loop
  broker.py     Alpaca (retry, MLEG)        exits.py     ±20%, stall, flatten, ratchet
  data.py       bars, HV, IV, cache         history.py   confidence from replayed history
  signal.py     the entry rule              gates.py     KB rules → (ok, sentence)
  contracts.py  pick the two legs           risk.py      the day's budget
  debate.py     bull/bear/judge             report.py    EOD markdown + Claude review
  ledger.py     append-only JSONL           web.py       read-only dashboard (web/static/app.{js,css})
docs/           CURRENT_SYSTEM.md · LEARNINGS.md · DESIGN.md · RUNBOOK.md · knowledge_base.md
tests/          hermetic, no network
launchd/        plists + heartbeat watchdog
state/ logs/ reports/   runtime (gitignored)
```
