"""status.py — the engine's shared snapshot (state/status.json). The dashboard
and the CLI only ever read this file; they never touch the engine."""
from __future__ import annotations

import json
import time
from datetime import datetime

from . import config
from . import llm as _llm


def write_status(engine, clk) -> None:
    now = engine.now()
    open_rows = []
    for p in engine.open.values():
        mid = p.last_mid or p.net_debit
        open_rows.append({"pos_id": p.pos_id, "sym": p.sym, "direction": p.direction, "setup": p.setup,
                          "long": p.long_occ, "short": p.short_occ, "qty": p.qty,
                          "entry": p.net_debit, "mid": round(mid, 2),
                          "pnl_usd": round(p.pnl_usd(mid), 2), "pnl_pct": round(p.pnl_pct(mid) * 100, 1),
                          "peak_pct": round(p.peak_pct * 100, 1), "stop": round(p.exit.stop, 2),
                          "tier": p.exit.tier, "opened_at": p.opened_at.isoformat(timespec="seconds")})
    doc = {
        "ts": now.isoformat(timespec="seconds"), "epoch": time.time(),
        "mode": "dry" if config.DRY_RUN else "paper",
        "connected": engine.broker.trading is not None,
        "key_prefix": engine.broker.key_prefix,
        "market_open": bool(clk and clk["is_open"]),
        "next_open": str(clk["next_open"]) if clk else None,
        "next_close": str(clk["next_close"]) if clk else None,
        "account": engine.account,
        "halted": config.HALT_PATH.exists(),
        # last Claude call failure ("" = fine). With the debate gate ON it fails CLOSED on
        # this, silently blocking every entry; with it OFF only the EOD review is lost
        "llm_error": _llm.last_error,
        "day": {"date": engine.day.date, "trades": engine.day.trades,
                "debit_spent_usd": round(engine.day.debit_spent_usd, 2),
                "realized_usd": round(engine.day.realized_usd, 2),
                "budget_usd": config.DAILY_BUDGET_USD, "max_trades": config.MAX_TRADES_PER_DAY,
                "loss_halt_usd": config.DAILY_LOSS_HALT_USD},
        "limits": {"per_trade_max_usd": config.PER_TRADE_MAX_USD, "tp_pct": config.TP_PCT,
                   "sl_pct": config.SL_PCT, "entry_window": f"{config.ENTRY_WINDOW_START_ET:%H:%M}–{config.ENTRY_WINDOW_END_ET:%H:%M} ET",
                   "flatten_at": f"{config.FLATTEN_AT_ET:%H:%M} ET", "dte": f"{config.DTE_MIN}–{config.DTE_MAX}",
                   "min_confidence": config.MIN_CONFIDENCE, "min_history_n": config.MIN_HISTORY_N,
                   "debate_model": config.DEBATE_MODEL, "debate_enabled": config.DEBATE_ENABLED,
                   "entry_start": f"{config.ENTRY_WINDOW_START_ET:%H:%M}", "entry_end": f"{config.ENTRY_WINDOW_END_ET:%H:%M}",
                   "gap_delay_start": f"{config.GAP_DELAY_START_ET:%H:%M}", "gap_delay_pct": config.GAP_DELAY_PCT,
                   "flatten": f"{config.FLATTEN_AT_ET:%H:%M}", "stall_min": config.STALL_MINUTES,
                   "max_open": config.MAX_OPEN, "same_dir_max": config.MAX_SAME_DIRECTION_OPEN,
                   "scan_sec": config.SCAN_INTERVAL_SEC, "setups": list(config.SETUPS_ENABLED),
                   "underlyings": list(config.UNDERLYINGS)},
        "open": open_rows,
        "signals": engine.last_signal,
        "history": {sym: {st: {d: h.as_dict() for d, h in by_dir.items()} for st, by_dir in hrs.items()}
                    for sym, hrs in engine.hit_rates.items()},
    }
    tmp = config.STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, default=str))
    tmp.replace(config.STATUS_PATH)
