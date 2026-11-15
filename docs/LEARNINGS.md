# Learnings carried from AlpacaTrader (May–Nov 2026) → where each one is enforced here

Source material: `_archive/AlpacaTrader/{ANALYSIS_LOG,TODO,CONTEXT,TUNING_NOTES,GO_LIVE_CHECKLIST}.md`,
`~/.spy_trader/{slippage,kb_relaxed,journal}.jsonl`, `gate_stats.json`, `failure_log.json`,
45 `eod_*.json` coach reviews, the Claude memory notes, and the real-fill ledger.
Every row names the code that enforces it, so this is a checklist, not a memoir.

## 1. Edge — what the data actually said

| Learning | Evidence | Enforced by |
|---|---|---|
| **The only signal with real directional edge was `vwap_momentum`** (55–60% hit at 30–60 min, +0.62 ATR, all 6 symbols, 3-yr Polygon). Every *naked-option* wrapper lost money (S0 PF 0.92, S1 0.41); shares preserved it (PF 1.38 OOS). Debit spreads were never validly tested (S2 harness bug). | ANALYSIS_LOG 2026-05-18 "FORK A", "STRUCTURE COMPARISON" | Ported as `signal.evaluate_vwap_momentum`; measured on 1,372 sessions via `history.replay(setup=…)`; the engine takes the setup with the best Wilson lower bound (`engine.scan`). This bot's *vehicle* is the spread, which is the untested S2 — so the first weeks are the S2 test. |
| `trend_cont` and `gap_fade` were noise (PF 0.49 / 0.46, ~48% hit). Removing the worst signal did not make the rest positive. | ANALYSIS_LOG 2026-05-17/18 | Not ported. Our own `trend_pullback` measured 40–60% / avg move ≈ 0 on 1,372 sessions — same verdict, kept only because it is measured and shown honestly. |
| Tight ATR stops (≤1.5×) are refuted — they noise-stop volatile options. Gentle profit protection (BE at +5%, trail 30%, lock +10% at +20%) beat the fixed baseline OOS on 2 of 3 strategies and lowered max-DD. | exit sweep 2026-05-17; exit-ladder backtest 2026-05-31 | `exit_engine.py` L1 config is the live ratchet (`config.EXIT_*`). |
| +80% / −50% option exits were −EV; the operator set ±20% on net debit. | project-options-20pct | `TP_PCT = SL_PCT = 0.20`. Honest math: symmetric ±20% needs >50% hits *after* costs; measured hits are 46–55%. Expect ≈ 0 until a setup proves better. |
| 80/20 profit rule is a premium-*seller* rule; inapplicable to debit buyers. | ANALYSIS_LOG 2026-05-18 | Not implemented. |
| An "85% confidence" that is a rule-count is misread as win probability. | project-confidence-calibration | `history.HitRate.label()` → "53% hit rate, n=273, 95% CI 47–59%"; the dashboard shows exactly that, per setup and direction. |
| KB §22: implied vol exceeds realised ~70% of months; buying premium blind is −EV. The old router fed an HV *level* into an IV-*rank* gate and bought unconditionally. | project-ivr-input-defect | `vol_edge.long_premium_ok` (carried) as the `§22 vol_edge` gate; **missing IV refuses**. `iv_rank` only after 20 logged sessions (`IV_HISTORY_MIN_DAYS`). |
| Paper days with 180-0 / 198-3 win rates were fill-assumption artefacts (241 KB relaxations, market orders at 5–21% spreads). | eod_2026-06-02/03 + kb_relaxed.jsonl | No relaxation flags exist in this codebase. Limit orders only (`broker.open_spread`). Reviewer prompt names the artefact explicitly. |

## 2. Execution & fills — the real killer

| Learning | Evidence | Enforced by |
|---|---|---|
| **2026-06-04 −$4,157: the losses were 225 bps average slippage on relaxed illiquid fills, not the stops; the coach said "tighten stops" — wrong.** | project-eod-risk-dialdown, slippage.jsonl (median 750 bps on debit-spread legs) | Fill-quality circuit breaker `gates.slippage_ok` (`SLIPPAGE_HALT_BPS=300` over last 5 fills). Slippage logged on every fill (`ledger.fill(expected_mid, slippage)`), shown in the report. Reviewer is told to read the slippage line before blaming entries or stops. |
| §9 liquidity per leg (5% of mid) rejected every cheap OTM short leg; applied to the *spread* it is the right test. | option-cache measurement 2026-11-14 | `gates.spread_cost` (both legs' quote width ≤ 10% of debit ≈ 5% one-way) + per-leg OI/two-sided check. |
| Never leg into a spread; a rejected leg must not leave a naked short. | screener_executor MLEG, CLAUDE.md rail 7 | `broker.open_spread/close_spread` are single atomic MLEG limit orders. |
| Opening/closing-minute fills are degraded. | TODO P1-E | Entries 10:00–11:30 only; flatten 15:45 (before the closing auction). |
| Fees are a silent tax at small size ($0.65/contract/leg). | TODO #9, CONTEXT trader profile | `FEE_PER_SPREAD_RT = 2.60` subtracted from every close; `fees_usd` in the ledger; rolling total in the report. |
| Verify against the **broker**, never the app's own store — the store was "confidently wrong every time". | project-deploy-exposed-defects | `engine.reconcile()` at boot and `reconcile_light()` every 5 min: orphans flattened, vanished spreads marked closed. |
| A stored order id is not proof the order exists; a stale-order sweep cancelled protective stops for years. | same | No sweep. The only cancel is of *our own* unfilled entry after `FILL_TIMEOUT_SEC`. Exits are engine-driven, intraday. |
| A dead price feed for one symbol silently left SMCI unmanaged to −20.6%. | project-profitability-diagnosis | `STALE_QUOTE_CLOSE_SEC = 600`: no two-sided spread quote for 10 min → close at last mid ×0.95 and log `stale_quote`. |

## 3. Risk & portfolio

| Learning | Evidence | Enforced by |
|---|---|---|
| **2026-06-05 −$2,549, 0W-20L: correlated concentration** — two lanes built a 20-name same-direction long book. SPY/QQQ are one bet. | project-eod-risk-dialdown | `MAX_SAME_DIRECTION_OPEN = 1` (`gates.same_direction_ok`); `MAX_OPEN = 2`. |
| Repeat losers: ORCL 7×/1W −$1,355, NVDA 10×/1W −$543, INTC 5×/0W. Cooldown "never fired" at 5 days. | profitability diagnosis; `SYMBOL_COOLDOWN` | `gates.cooldown_ok`: 2 stop-outs on a symbol+direction within 5 sessions → skip (`risk.recent_stops`, from the ledger, not a store). |
| Equal-dollar sizing; per-trade and daily hard caps; 2 trades/day; daily-loss circuit breaker that only blocks *new* entries. | project-sizing-caps, AUTO_EXECUTE rails, CONTEXT ($5K profile: ~$300/trade) | `risk.DayState.can_enter` + `contracts_for`: $500/trade, $1,500/day, 3 trades (2 open), −$750 halt (raised from $300/$600/2/−$300 on 2026-11-15); persisted to `state/day_state.json` so a restart can't reset it. |
| Top-3 trades carried the whole old ledger (3 of 166 = +$3,935; the rest −$2,509). | profitability diagnosis; GO-LIVE §1 | Report prints "top-3 trades = X% of net" and flags > 40%. |
| Macro-event days (FOMC 2 PM, 8:30 prints) turn 7-DTE options into coin flips. | TODO #5, GO-LIVE §3 | `gates.event_blackout`: operator-owned `EVENT_BLACKOUT_DATES` (2026 FOMC seeded — verify yearly) + first-Friday NFP rule. |
| Gap days: the first 30–60 min after a >1% gap is the highest-whipsaw window. | TODO #12 | `gates.entry_window(now, open_gap_pct)`: ≥1% gap → entries from 10:30. |
| `reason` in the old ledger was reverse-engineered from P&L, not the trigger. | profitability diagnosis | `ledger.close(reason=…)` is the literal trigger text from `exits.check`. |
| 59% of old ledger rows had no `setup`; `dry_run` was meaningless; the 2026-09-11 "847 closes" was duplicate rows. | ledger review; eod_2026-09-11 | `Ledger.write` enforces `setup`/`mode`; `report` counts one close per `pos_id`. |

## 4. Reliability — why the old system silently stopped

| Learning | Evidence | Enforced by |
|---|---|---|
| One-shot login at boot; a DNS blip → unauthenticated all day; exits gated on UI login state. | CURRENT_SYSTEM.md defect 1 | `broker.connect()` retries forever; the monitor *is* the loop; no UI state. |
| Watchdog kill-loop: double-launch vs KeepAlive, `lsof` matching browser clients, tight timeouts. | project-watchdog-killloop-fix | `launchd/watchdog.sh` reads the heartbeat file only, kills only, never relaunches. |
| Scheduler stalls on laptop sleep (3,231s, 7,317s) recovered but silently. | failure_log.json (44 watchdog restarts) | Every engine event is a ledger row; the report counts boots and tick errors; the dashboard shows status age in red past 120 s. |
| `logging.disable` at import time switched off the whole app's logging. | deploy-exposed defect 4 | Nothing here touches the root logger except `cli._logging`. |
| Two files held 37% of the code; the login bug sat next to a comment describing it. | CURRENT_SYSTEM.md | 20 modules, none over ~400 lines; `config.py` is the only place a limit lives. |
| The daily report said "nothing to analyse" for 60 days while the lane was dead. | daily_report.log | `report.render` always states engine events, decisions, and *why zero* (first failing gate per decision). |

## 5. Process — the discipline that produced the useful negative results

| Learning | Where it lives now |
|---|---|
| KB §12 (Davey): every strategy passes its own cost-robust walk-forward (PF ≥ 1.10 at 3 **and** 5 bp OOS) before live; params frozen during ≥4-week paper incubation; top-3 < 40% of P&L. | `GO_LIVE_CHECKLIST` concept retained: `PAPER_ONLY = True`, no live path. `spyqqq/research.py` is the sweep; `reports/research-*.md` is the record. |
| The best cell of a grid is optimistic; prefer parameters that hold across years. | `research.py` prints the by-year table under every grid. |
| "Trust but verify" on your own claims — the exit-sweep and S3 results were both retracted after a proper re-test. | Written into `docs/DESIGN.md` § Confidence; the reviewer prompt forbids recommending "trade more". |
| At $5K real money, friction decides: minimum premium ~$500, 3 PDT day-trades/week. | Not yet relevant (paper, $94K); recorded here so the sizing conversation starts from it when it is. |

## Book re-read 2026-11-15

See `docs/BOOK_LEARNINGS.md`: four rules applied (Natenberg leg choice, 10:05 start, witching blackout, rolling drawdown halt), nine confirmed, five research items.

## Still open (known, not done)

- VIX rate-of-change gate (TODO #11) — no VIX feed in this bot; HV5 regime via `vol_edge.vol_regime` is the available proxy, not wired as a gate.
- CPI dates for `EVENT_BLACKOUT_DATES` — operator adds from the BLS calendar.
- Debit-spread P&L backtest on the cached option bars (the S2 harness the old project never fixed) — daily bars can't resolve same-day exits; needs intraday option data.
