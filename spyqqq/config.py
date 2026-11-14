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
DAILY_BUDGET_USD      = 600.0    # total net debit allowed per day
PER_TRADE_MAX_USD     = 300.0    # KB §4 — max loss per trade (debit spread: max loss = debit)
MAX_TRADES_PER_DAY    = 2
MAX_OPEN              = 2        # one per underlying
DAILY_LOSS_HALT_USD   = 300.0    # realised loss today ≥ this → no new entries, keep managing

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
ENTRY_WINDOW_START_ET = time(10, 0)    # KB §3: never the first 30 min
ENTRY_WINDOW_END_ET   = time(11, 30)
SCAN_INTERVAL_SEC     = 60
MONITOR_INTERVAL_SEC  = 10
HEARTBEAT_STALE_SEC   = 120            # watchdog kills the daemon past this

# ── Contract selection ───────────────────────────────────────────────────────
DTE_MIN, DTE_MAX      = 7, 21
SPREAD_WIDTH_STEPS    = 2              # short leg this many $1 strikes beyond the long (2-wide spread)
OPT_MIN_OI            = 200            # KB §9
OPT_MAX_BID_ASK_PCT   = 0.25           # per-leg quote sanity (a cheap OTM short leg is wide in % terms)
OPT_MAX_SPREAD_COST_PCT = 0.10         # KB §9 applied to the SPREAD: both legs' quote widths ≤ 10% of debit (≈5% one-way)
SPREAD_RATIO_LO       = 0.25           # KB §5: debit ≥ 25% of width …
SPREAD_RATIO_HI       = 0.45           # … and ≤ 45% of width
LIMIT_PAD_FRAC        = 0.25           # pay mid + 25% of the half-spread (marketable limit)
FILL_TIMEOUT_SEC      = 45

# ── Signal ───────────────────────────────────────────────────────────────────
TREND_MA_DAYS         = 20             # daily close vs 20d SMA sets the allowed direction
PULLBACK_EMA          = 9              # 5-min EMA the pullback must reclaim
RSI_LEN               = 14
RSI_MIN, RSI_MAX      = 40, 65         # KB: no chasing (bull); mirrored for bear
VOL_RATIO_MIN         = 1.0            # reclaim bar volume vs the prior 30 min
MIN_CONFIDENCE        = 0.55           # historical hit-rate (Wilson lower bound) floor
MIN_HISTORY_N         = 30             # need this many past occurrences to trust the rate

# ── Vol gate (KB §22) ────────────────────────────────────────────────────────
VOL_EDGE_REQUIRED     = True
MIN_VOL_EDGE_PTS      = 1.0
IV_HISTORY_MIN_DAYS   = 20             # iv_rank only once we've logged this many sessions

# ── LLM gates ────────────────────────────────────────────────────────────────
DEBATE_ENABLED        = True
DEBATE_MODEL          = os.environ.get("SPYQQQ_MODEL", "claude-opus-5-5")
REVIEW_MODEL          = os.environ.get("SPYQQQ_REVIEW_MODEL", "claude-opus-5-5")
DEBATE_MIN_CONFIDENCE = 0.65

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).resolve().parent.parent
STATE_DIR   = ROOT / "state"
LOG_DIR     = ROOT / "logs"
REPORT_DIR  = ROOT / "reports"
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
