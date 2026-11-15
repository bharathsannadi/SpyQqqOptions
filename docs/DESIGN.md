# Design

One process, one thread, one loop. The dashboard is a separate process that only
reads files. If the dashboard dies the bot doesn't notice; if the bot dies the
dashboard shows a red "updated 140s ago" pill.

```
                 ┌──────────────── spyqqq run (launchd: com.spyqqq) ────────────────┐
                 │  broker.connect()  ── retry forever, 5s→60s backoff              │
                 │  reconcile()       ── ledger ⇄ broker; orphans flattened + logged │
                 │  refresh_history() ── 130 sessions × 5-min bars → hit rates       │
                 │  loop every 1s:                                                   │
                 │    heartbeat → state/heartbeat        (watchdog kills if >120s)   │
                 │    monitor  (10s)  exits.check() on each open spread              │
                 │    scan     (60s, 10:00–11:30 ET)  signal → gates → debate → order│
                 │    15:45 ET  flatten_all → report.write()                         │
                 │    status   (5s)   state/status.json                              │
                 └───────────────────────────────────────────────────────────────────┘
                              │ writes                       │ writes
                     state/ledger.jsonl               reports/YYYY-MM-DD.md
                              │ reads                        │ reads
                 ┌─────────── spyqqq web (launchd: com.spyqqq.web, :5050) ───────────┐
                 │  /            dashboard (polls /api/status every 15s)             │
                 │  /api/status  /api/ledger?day=  /api/reports  /api/report/<day>   │
                 └───────────────────────────────────────────────────────────────────┘
```

## The pipeline for one scan (`engine.scan`)

1. `signal.daily_trend` — close vs 20-day SMA (required by trend_pullback only).
2. `signal.evaluate_all` — every enabled setup on today's closed 5-min bars:
   - `trend_pullback`: pullback through EMA9, reclaim bar in the trend direction,
     RSI14 in the no-chase band, volume ≥ 0.8× the prior 30 min.
   - `vwap_momentum` (ported from AlpacaTrader — the one signal with measured edge):
     close vs session VWAP, >50% of recent bars on that side, EMA9 vs EMA21,
     MACD(12,26,9) histogram sign, volume ≥ 1.0× trailing 20 bars, closing in the
     direction. EMAs/MACD warm up on the prior session's bars.
   If several fire, the setup with the best historical Wilson lower bound is taken.
3. Gates, cheapest first, each returning `(ok, KB sentence)`:
   - `§3 entry_window` (entries from 10:30 on a ≥1% opening gap)
   - `§17 event_blackout` — FOMC decision days (operator list) and first-Friday NFP
   - `§12 confidence` — `history.replay` of the same rule over the cached sessions;
     Wilson 95% lower bound ≥ `MIN_CONFIDENCE` with `n ≥ MIN_HISTORY_N`
   - `§7 cooldown` — 2 stop-outs on this symbol+direction in 5 sessions → skip
   - `§5 same_direction` — SPY and QQQ are one bet; max 1 open per direction
   - `§9 fill_quality` — avg slippage of the last 5 real fills < 300 bps
   - `§22 vol_edge` — `0.45·HV5 + 0.35·IV30 + 0.20·HV30 > IV30 + 1pt`; missing IV refuses
   - contract gates from `contracts.select`: `§3 dte`, `§9 liquidity` (OI, two-sided
     quote), `§9 spread_cost` (both legs' quote width ≤ 10% of debit), `§5 spread_ratio`
     (debit 25–45% of width; long 2–3 OTM on a 5-wide, per the option-cache measurement)
   - `§4 day_risk` — `risk.DayState.can_enter`: per-trade cap, daily budget, trade count,
     max open, loss halt, operator HALT file
   - `debate` — bull / bear / judge on Opus 5.5; fails **closed** on any error
4. Accepted → one atomic MLEG limit at `net_mid + 0.25 × half-spreads`; 45s fill
   wait; cancel if unfilled. Every step is a ledger row.

Every 5 min during the session `engine.reconcile_light` compares open spreads with the
broker: vanished spreads are marked closed, unknown option legs are flattened and logged.
A spread with no two-sided quote for 10 min is closed at its last mid ×0.95.

## Exits (`exits.check`, every 10s)

1. `FLATTEN_AT_ET` 15:45 → close.
2. `+TP_PCT` (+20%) → close.
3. Stall: held ≥ 90 min and still < +5% → close.
4. `ExitEngine` ratchet: initial stop −20%; at +5% stop moves to breakeven and trails
   30% off the high-water mark; at +20% a +10% floor is locked. Never moves down.

## Confidence ("based on historic data")

`history.replay` walks each cached session bar by bar inside the entry window,
calls the *same* `signal.evaluate`, takes the first signal of the day, and resolves it
by first touch of ±0.30% on the underlying before 15:45 (else the sign at 15:45).
Reported as hit rate, n, and the Wilson 95% interval; the gate uses the lower bound.
The number means: "of the last n sessions where this rule fired on this symbol in
this direction, the underlying then moved our way this often." It is not a win
probability for the spread.

## What is deliberately absent

No resting broker stops (intraday only; the engine *is* the stop). No UI state in the
engine. No eventlet/greenlets. No yfinance. No ChromaDB. No live-trading path:
`PAPER_ONLY = True` and `broker.connect` raises on a live key.
