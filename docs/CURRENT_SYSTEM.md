# The system this one replaces — AlpacaTrader, as of 2026-11-14

Archived at `/Users/bsannadi/Desktop/bharath/_archive/AlpacaTrader`, git tag
`archive/final-2026-11-14` on `github.com/bharathsannadi/AlpacaTrader`. This page is
the post-mortem that justified the rewrite; keep it with the new code.

## What it was

A Flask + Socket.IO dashboard (`scripts/app.py`, 4,838 lines) wrapping an intraday
options bot (`scripts/spy_auto_trader.py`, 5,793 lines) that grew, over 337 commits
(223 in May 2026, 102 in June, 2 in July, 10 in September, 0 after), into:

- a multi-strategy stock/ETF screener with a KB-ranked merged pick list,
- a stock lane (±2% exits, ~$5,000 equal-dollar sizing, Connors RSI-2 daily),
- an options lane (debit spreads, ±20% on net debit, $600 cap, SPY/QQQ only since Sep),
- a Claude bull/bear debate gate, a ChromaDB trade memory, a news filter,
- Polygon + yfinance data, a separate charts server (:5001), a desktop app,
- 5 launchd agents + a watchdog, 28,769 lines of Python in 72 files.

Paper trading only; live was gated and never approved.

## What was measured on 2026-11-14

**Realised ledger, 166 closes, 2026-06-17 → 2026-09-11 (paper):**

| | |
|---|---|
| Net | +$1,317.86 |
| Win rate | 44.0% (73/166) |
| Profit factor | 1.13 |
| Avg win / avg loss | +$157.71 / −$109.63 |
| Monthly | Jun −2,116 · Jul −2,148 · **Aug +5,765** · Sep −183 |
| Top symbols | PLTR +2,444 · NOW +1,575 · CRM +1,574 · ORCL −1,355 · SMCI −978 |

August was the whole edge; without it the three other months net −$4,450.
`setup` was empty on 98 of 166 rows (59%); `dry_run` was `False` on every row.

**Account:** equity $104,960.84 at the last successful auto-login (Sep 11) → $94,342.78
on Nov 14 (−$10,618, −10.1%).

**Open at the broker on Nov 14 (paper):**

| Symbol | Qty | Entry | Last | Unrealised |
|---|---|---|---|---|
| NVDA | 100 | 227.30 | 162.85 | −$6,445 (−28.4%) |
| INTC | 49 | 102.85 | 59.66 | −$2,116 (−42.0%) |
| PLTR | 27 | 179.92 | 167.99 | −$322 (−6.6%) |
| CVNA | 67 | 73.74 | 72.37 | −$92 (−1.9%) |

Under a ±2% rule. NVDA at $22,730 notional was ~4.5× the sizing rule and was not in
the Sep 13 snapshot — it did not come from the sized lane.

## The defects, with evidence

1. **One-shot login that fails silently.** `app.py:4817` runs `_auto_login` once, 3s
   after boot; on any exception it logs and `return`s (`app.py:4755`). `position_monitor`
   gates on `state["logged_in"]` (`app.py:1705`). On 2026-11-13 the app booted at 10:00 ET,
   DNS was down at 10:23 (`Failed to resolve 'paper-api.alpaca.markets'`), and the app
   then ran all day serving a dashboard with no authenticated client and no exit
   management. The connectivity probe logged "recovered" at 12:28 the next day and did
   not re-login. `app.py:2300` has a comment admitting positions stay "UNMANAGED until
   the next server restart's `_auto_login`."
2. **The entry lane had been dead for two months.** `data/auto_exec_state.json` stuck on
   `2026-09-11`; `logs/screener_executor.trail` last written Sep 11 12:35; the daily report
   printed "No real closes in this window. Nothing to analyse." every day until Nov 14 —
   it could not tell the difference between *no setups* and *not running*.
3. **Stale-order sweep cancelled protective stops** (fixed Sep 11 in `f0e1a7e`/`7ff4ebc`,
   after years of doing so for `daily_trader`).
4. **Ledger couldn't attribute P&L**: no `setup` on 59% of rows, meaningless `dry_run`.
5. **Scheduler stalls on laptop sleep** (3,231s on Oct 17, 7,317s on Nov 12) recovered by
   respawn, but silently; `com.alpacatrader` showed `runs = 6`, last exit −9.
6. **Two files held 37% of the code.** The login bug sat next to a comment describing it.

## What was carried into SpyQqqOptions

`vol_edge.py` (KB §22 gate), `exit_engine.py` (validated profit ladder),
`credentials.py`, the validators from `security.py`, the atomic-MLEG order pattern
from `screener_executor.py`, the three-call structure of `debate.py`, `knowledge_base.md`,
and the `test_vol_edge` / `test_exit_engine` / `test_credentials` suites. ~1,500 lines.

Everything else — Flask/Socket.IO UI, screener, stock lane, Connors daily, ChromaDB,
news filter, yfinance, Polygon, charts server, desktop app, backtests — was dropped.
