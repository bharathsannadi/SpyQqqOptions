"""config.py — every tunable in one place.

Change a number here, restart the daemon, and the EOD report names the constant
it would change next. Nothing else in the package hard-codes a limit.

Env overrides (optional): SPYQQQ_DRY_RUN=1, SPYQQQ_MODEL, SPYQQQ_REVIEW_MODEL.
"""
from __future__ import annotations

import os
from datetime import time
from pathlib import Path

# ── Identity / safety ────────────────────────────────────────────────────────
PAPER_ONLY            = True                 # v1 has no live path; broker.py refuses paper=False
UNDERLYINGS           = ("SPY", "QQQ")
DRY_RUN               = os.environ.get("SPYQQQ_DRY_RUN", "0") == "1"   # decide + log, never order

# ── The daily budget ("limited amount for a day") ────────────────────────────
DAILY_BUDGET_USD      = 1500.0   # total net debit allowed per day (raised 600→1500 on 2026-11-15, operator)
PER_TRADE_MAX_USD     = 500.0    # KB §4 — max loss per trade (debit spread: max loss = debit); 300→500
MAX_TRADES_PER_DAY    = 3        # 2→3: a third entry only after one of the two open spreads closes
MAX_OPEN              = 2        # one per underlying
DAILY_LOSS_HALT_USD   = 750.0    # realised loss today ≥ this → no new entries, keep managing (half the budget, as before)
ROLLING_LOSS_HALT_USD = 3000.0   # net realised over the last ROLLING_LOSS_SESSIONS ≤ −this → no new entries (BK-6: Elder 6%-month rule, budget-relative)
ROLLING_LOSS_SESSIONS = 10
MAX_SAME_DIRECTION_OPEN = 1      # SPY+QQQ are one bet (ρ≈0.9): the 2026-06-05 −$2,549 day was 20 same-way longs
SLIPPAGE_HALT_BPS     = 300.0    # avg fill slippage over the last SLIPPAGE_WINDOW fills ≥ this → halt entries
SLIPPAGE_WINDOW       = 5        # (2026-06-04: 225 bps avg slippage on relaxed fills was the killer, not stops)
COOLDOWN_STOPS        = 2        # ≥ this many stop-outs on a symbol+direction …
COOLDOWN_SESSIONS     = 5        # … within this many sessions → skip that setup (ORCL 7×/1W, NVDA 10×/1W)
FEE_PER_SPREAD_RT     = 2.60     # $0.65/contract/leg × 2 legs × open+close — subtracted from every close (TODO #9)

# ── Exits (on net debit) ─────────────────────────────────────────────────────
TP_PCT                = 0.20     # take profit at +20% of net debit
SL_PCT                = 0.20     # stop at −20% of net debit
STALL_MINUTES         = 90       # green-but-stalled (< +5%) after N min → close
FLATTEN_AT_ET         = time(15, 45)   # nothing held overnight
# ExitEngine ladder (carried from AlpacaTrader, validated 2026-05-31): breakeven
# at +5%, trail 30% off HWM, lock +10% at +20%. SL_PCT above is the initial stop.
EXIT_BE_TRIGGER       = 0.05
EXIT_TRAIL            = 0.30
EXIT_FLOOR2_GAIN      = 0.20
EXIT_FLOOR2_LOCK      = 0.10

# ── Session windows (ET) ─────────────────────────────────────────────────────
ENTRY_WINDOW_START_ET = time(10, 5)    # KB §3: never the first 30 min; 10:05 so the 10:00 ET release bar (ISM/JOLTS/UMich) is closed first (BK-3)
ENTRY_WINDOW_END_ET   = time(12, 30)   # 11:30→12:30 on 2026-11-17 (operator: "best guess" balance; 2025-26 replay ≈107 trades/yr)
GAP_DELAY_PCT         = 1.0            # |open gap| ≥ this % → entries start 30 min later (TODO #12 gap-day)
GAP_DELAY_START_ET    = time(10, 30)
STALE_QUOTE_CLOSE_SEC = 600            # no two-sided spread quote for this long → close at last mid (a dead
                                       # feed silently left SMCI at −20.6% in the old bot)
RECONCILE_EVERY_SEC   = 300            # verify open spreads against the BROKER, not the store (2026-09-11 lesson)
# Macro-event blackout (TODO #5 / GO_LIVE §3). No entries on these dates. FOMC 2026 decision
# days seeded from the published schedule — VERIFY each January. NFP = first Friday (auto).
# Add CPI release dates here when the BLS calendar is out; the operator owns this list.
EVENT_BLACKOUT_DATES  = {"2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
                         "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"}
NFP_BLACKOUT          = True
WITCHING_BLACKOUT     = True             # 3rd Friday of Mar/Jun/Sep/Dec: index futures+options expiry and SPY/QQQ ex-dividend (BK-4)
SCAN_INTERVAL_SEC     = 60
MONITOR_INTERVAL_SEC  = 10
HEARTBEAT_STALE_SEC   = 120            # watchdog kills the daemon past this

# ── Contract selection ───────────────────────────────────────────────────────
DTE_MIN, DTE_MAX      = 7, 21
# Measured on the cached 2022–26 SPY/QQQ option bars (7–21 DTE): an ATM 2-wide debit
# spread costs a median 52–54% of width — outside KB §5 (25–45%) 90% of the time.
# Long 2–3 strikes OTM on a 5-wide lands inside the band 60–73% of the time.
SPREAD_WIDTH_STEPS    = 5              # short leg this many $1 strikes beyond the long
LONG_OFFSETS          = (1, 2, 3)      # try the long nearest the money first (Natenberg: when IV is low — our §22 regime — buy the ATM side), fall back OTM to satisfy §5 (BK-1)
OPT_MIN_OI            = 200            # KB §9
OPT_MAX_BID_ASK_PCT   = 0.25           # per-leg quote sanity (a cheap OTM short leg is wide in % terms)
OPT_MAX_SPREAD_COST_PCT = 0.10         # KB §9 applied to the SPREAD: both legs' quote widths ≤ 10% of debit (≈5% one-way)
SPREAD_RATIO_LO       = 0.25           # KB §5: debit ≥ 25% of width …
SPREAD_RATIO_HI       = 0.45           # … and ≤ 45% of width
LIMIT_PAD_FRAC        = 0.25           # pay mid + 25% of the half-spread (marketable limit)
FILL_TIMEOUT_SEC      = 45

# ── Signal ───────────────────────────────────────────────────────────────────
SETUPS_ENABLED        = ("vwap_momentum",)   # 2026-11-17: trend_pullback dropped — lost before costs in 3 of 4 sym×dir buckets over 2021-26
VWAP_REQUIRE_TREND    = True             # 2026-11-17: vwap_momentum only WITH the daily 20d-SMA trend (best out-of-sample variant)
VWAP_MIN_VOL_RATIO    = 1.0            # vwap_momentum: bar volume vs trailing 20-bar average
TREND_MA_DAYS         = 20             # daily close vs 20d SMA sets the allowed direction
PULLBACK_EMA          = 9              # 5-min EMA the pullback must reclaim
RSI_LEN               = 14
RSI_MIN, RSI_MAX      = 40, 65         # KB: no chasing (bull); mirrored for bear
VOL_RATIO_MIN         = 0.8            # reclaim bar volume vs the prior 30 min
# Historical-confidence floor. LEARNING-PHASE values (2026-11-14): measured on 130
# sessions the rule hits 40–60% (n≈12–20 per symbol/direction) — a 55% lower-bound
# floor would never trade. 0.35 blocks only the demonstrably bad (QQQ bear: 17%).
# Raise toward 0.55 as the sample grows; the dashboard shows the live number.
MIN_CONFIDENCE        = 0.35           # Wilson 95% LOWER bound of the hit rate
MIN_HISTORY_N         = 10             # past occurrences needed before trading the setup

# ── Vol gate (KB §22) ────────────────────────────────────────────────────────
VOL_EDGE_REQUIRED     = True
MIN_VOL_EDGE_PTS      = -8.0           # 2026-11-17 PAPER best guess (was +1.0): a debit spread SELLS the same rich IV on its
                                       # short leg, so only refuse when IV is >8 vol pts over forecast (stressed). Untested — no historical IV.
IV_HISTORY_MIN_DAYS   = 20             # iv_rank only once we've logged this many sessions

# ── LLM gates ────────────────────────────────────────────────────────────────
DEBATE_ENABLED        = False          # 2026-11-15: OFF until Anthropic credits exist (was failing CLOSED → zero trades)
DEBATE_MODEL          = os.environ.get("SPYQQQ_MODEL", "claude-opus-5-5")
REVIEW_MODEL          = os.environ.get("SPYQQQ_REVIEW_MODEL", "claude-opus-5-5")
DEBATE_MIN_CONFIDENCE = 0.65

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).resolve().parent.parent
STATE_DIR   = Path(os.environ.get("SPYQQQ_STATE_DIR", ROOT / "state"))     # overridable so a fixture can drive the dashboard
LOG_DIR     = ROOT / "logs"
REPORT_DIR  = Path(os.environ.get("SPYQQQ_REPORT_DIR", ROOT / "reports"))
DOCS_DIR    = ROOT / "docs"
LEDGER_PATH     = STATE_DIR / "ledger.jsonl"
DAY_STATE_PATH  = STATE_DIR / "day_state.json"
STATUS_PATH     = STATE_DIR / "status.json"
HEARTBEAT_PATH  = STATE_DIR / "heartbeat"
HALT_PATH       = STATE_DIR / "HALT"
IV_HISTORY_PATH = STATE_DIR / "iv_history.jsonl"
BARS_CACHE_DIR  = STATE_DIR / "bars"
KB_PATH         = DOCS_DIR / "knowledge_base.md"

for _d in (STATE_DIR, LOG_DIR, REPORT_DIR, BARS_CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)
