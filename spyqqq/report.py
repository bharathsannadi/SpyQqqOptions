"""report.py — the EOD page the operator reviews every day.

Always says what the engine *did* — sessions, decisions, every rejection by KB §
— so a zero-trade day explains itself. Ends with one Claude paragraph naming the
constant it would change tomorrow.
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional

from . import config, llm
from .ledger import Ledger

log = logging.getLogger(__name__)
_SEC = re.compile(r"KB §(\d+)")

_REVIEW_SYSTEM = (
    "You are the end-of-day reviewer for a PAPER-money SPY/QQQ debit-spread bot. "
    "You get today's ledger summary. Write at most 180 words, no preamble, no headers, plain prose: "
    "(1) what happened and whether the rules were followed; (2) the single most useful change for "
    "tomorrow, naming the exact config constant and the new value, or 'no change' with the reason; "
    "(3) one sentence on what the rolling stats say about the edge — treat paper results as weak evidence. "
    "Hard rules learned from the previous system: never suggest raising DAILY_BUDGET_USD, PER_TRADE_MAX_USD "
    "or MAX_TRADES_PER_DAY; never recommend 'taking more of the signals' or loosening a gate; before blaming "
    "entries or stops, check the fill-quality (slippage) line and the gate-rejection list — on 2026-06-04 the "
    "losses were slippage on illiquid fills and the coach wrongly said 'tighten stops'. A perfect win rate on "
    "paper is a fill-assumption artefact, not skill. If fewer than 20 closes exist, say the sample is too small.")


def _money(v: float) -> str:
    return f"${v:,.2f}" if v >= 0 else f"−${abs(v):,.2f}"


def summarize(day: str, ledger: Ledger) -> dict:
    rows = list(ledger.rows(day))
    dec = [r for r in rows if r["kind"] == "decision"]
    fills = [r for r in rows if r["kind"] == "fill"]
    closes_by_id: dict = {}
    for r in rows:
        if r["kind"] == "close" and r.get("exit") is not None:
            closes_by_id[r.get("pos_id") or r["ts"]] = r      # one close per position (old journal had 3× dupes)
    closes = list(closes_by_id.values())
    eng = [r for r in rows if r["kind"] == "engine"]
    rej_by_sec: Counter = Counter()
    rej_text: Counter = Counter()
    for r in dec:
        if r.get("accepted"):
            continue
        for g in r.get("gates", []):
            if not g["ok"]:
                m = _SEC.search(g["text"])
                rej_by_sec[f"§{m.group(1)}" if m else g["gate"]] += 1
                rej_text[g["text"][:140]] += 1
                break   # first failing gate is the reason
    pnl = sum(r.get("pnl_usd", 0) or 0 for r in closes)
    wins = [r for r in closes if (r.get("pnl_usd") or 0) > 0]
    slip = [r["slippage"] for r in fills if r.get("slippage") is not None and not r.get("dry")]
    deb_vs = []
    for c in closes:
        d = next((x for x in dec if x.get("accepted") and x["sym"] == c["sym"]), None)
        if d and d.get("debate"):
            deb_vs.append((d["debate"].get("confidence"), c.get("pnl_usd")))
    return {"rows": len(rows), "decisions": len(dec), "accepted": sum(1 for r in dec if r.get("accepted")),
            "fills": len(fills), "closes": closes, "pnl": pnl, "wins": len(wins),
            "rej_by_sec": rej_by_sec.most_common(), "rej_text": rej_text.most_common(6),
            "slippage": slip, "debate_vs_outcome": deb_vs, "engine": eng,
            "signals_seen": Counter(r["sym"] for r in dec).most_common()}


def rolling(ledger: Ledger, days: int = 20) -> dict:
    since = (datetime.now() - timedelta(days=days * 1.5)).strftime("%Y-%m-%d")
    by_id: dict = {}
    for r in ledger.rows():
        if r["kind"] == "close" and r["ts"][:10] >= since and r.get("exit") is not None:
            by_id[r.get("pos_id") or r["ts"]] = r
    closes = list(by_id.values())
    pnl = [r.get("pnl_usd", 0) or 0 for r in closes]
    top3 = sum(sorted(pnl, reverse=True)[:3])
    wins = [p for p in pnl if p > 0]; losses = [p for p in pnl if p <= 0]
    by_day: dict[str, float] = defaultdict(float)
    for r in closes:
        by_day[r["ts"][:10]] += r.get("pnl_usd", 0) or 0
    return {"n": len(pnl), "net": sum(pnl), "win_rate": (len(wins) / len(pnl)) if pnl else None,
            "pf": (sum(wins) / abs(sum(losses))) if losses and sum(losses) else None,
            "avg_win": (sum(wins) / len(wins)) if wins else None,
            "avg_loss": (sum(losses) / len(losses)) if losses else None,
            "days": len(by_day), "green_days": sum(1 for v in by_day.values() if v > 0),
            "top3_share": (top3 / sum(pnl)) if sum(pnl) > 0 else None,
            "fees": sum(r.get("fees_usd", 0) or 0 for r in closes)}


def render(day: str, ledger: Ledger, account: Optional[dict] = None,
           hit_rates: Optional[dict] = None, day_state=None) -> tuple[str, str]:
    s = summarize(day, ledger)
    r = rolling(ledger)
    L = [f"# SPY/QQQ Options — Daily Report {day}", ""]
    L.append(f"**Mode:** {'DRY RUN' if config.DRY_RUN else 'PAPER'}  ·  "
             f"**Equity:** {_money(account['equity']) if account else '—'}  ·  "
             f"**Budget used:** {_money(day_state.debit_spent_usd) if day_state else '—'} / {_money(config.DAILY_BUDGET_USD)}")
    L.append("")
    L.append("## What the engine did")
    boots = [e for e in s["engine"] if e.get("event") in ("boot", "ready")]
    errs = [e for e in s["engine"] if e.get("event") == "tick_error"]
    L.append(f"- engine events: {len(s['engine'])} (boots {len(boots)}, tick errors {len(errs)})")
    L.append(f"- decisions evaluated: {s['decisions']} (accepted {s['accepted']}) — "
             f"signals by symbol: {', '.join(f'{k} {v}' for k, v in s['signals_seen']) or 'none'}")
    L.append(f"- fills: {s['fills']}  ·  closes: {len(s['closes'])}")
    if s["decisions"] == 0:
        L.append("- **Zero signals fired.** Either no pullback-reclaim set up inside "
                 f"{config.ENTRY_WINDOW_START_ET:%H:%M}–{config.ENTRY_WINDOW_END_ET:%H:%M} ET, or the daily "
                 "trend filter was flat (close within 0.1% of the 20d SMA).")
    L.append("")
    L.append("## Why trades were refused (first failing gate)")
    if s["rej_by_sec"]:
        for k, v in s["rej_by_sec"]:
            L.append(f"- {k}: {v}")
        L.append("")
        for t, n in s["rej_text"]:
            L.append(f"  - ×{n} {t}")
    else:
        L.append("- no rejections")
    L.append("")
    L.append("## Trades")
    if s["closes"]:
        L.append("| sym | dir | legs | qty | entry | exit | P&L | peak | held | reason |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for c in s["closes"]:
            L.append(f"| {c['sym']} | {c.get('direction','')} | {c.get('long_occ','')}/{c.get('short_occ','')} | "
                     f"{c.get('qty')} | {c.get('entry')} | {c.get('exit')} | {_money(c.get('pnl_usd',0))} | "
                     f"{c.get('peak_pct')}% | {c.get('held_min')}m | {c.get('reason','')} |")
        L.append("")
        L.append(f"**Day P&L:** {_money(s['pnl'])}  ·  wins {s['wins']}/{len(s['closes'])}")
    else:
        L.append("- no closed trades")
    if s["slippage"]:
        avg = sum(s["slippage"]) / len(s["slippage"])
        L.append(f"- slippage vs model mid: avg {avg:+.3f} per spread over {len(s['slippage'])} fills")
    if s["debate_vs_outcome"]:
        L.append("- debate confidence vs outcome: " + ", ".join(
            f"{(c or 0):.0%}→{_money(p or 0)}" for c, p in s["debate_vs_outcome"]))
    L.append("")
    L.append("## Historical confidence (what the signal rule did on past sessions)")
    if hit_rates:
        for sym, hrs in hit_rates.items():
            for st, by_dir in hrs.items():
                for d, h in by_dir.items():
                    lab = h.label() if hasattr(h, "label") else h.get("label")
                    L.append(f"- {sym} {st} {d}: {lab}")
    else:
        L.append("- not computed")
    L.append("")
    L.append("## Rolling 20 sessions")
    if r["n"]:
        L.append(f"- {r['n']} closes over {r['days']} days ({r['green_days']} green) · net {_money(r['net'])} "
                 f"(fees {_money(r['fees'])}) · win rate {r['win_rate']:.0%} · PF {(r['pf'] or 0):.2f} · "
                 f"avg win {_money(r['avg_win'] or 0)} / avg loss {_money(r['avg_loss'] or 0)}")
        if r["top3_share"] is not None:
            flag = " ⚠ outlier-carried (GO-LIVE: top-3 must be < 40%)" if r["top3_share"] > 0.40 else ""
            L.append(f"- top-3 trades = {r['top3_share']:.0%} of net{flag}")
    else:
        L.append("- no closed trades yet")
    L.append("")
    body = "\n".join(L)
    review = llm.ask(_REVIEW_SYSTEM, body, config.REVIEW_MODEL, max_tokens=600, effort="high")
    L.append("## Review")
    L.append(review or "_review unavailable (no ANTHROPIC_API_KEY or call failed)_")
    L.append("")
    return "\n".join(L), review or ""


def write(day_str: str, ledger: Ledger, account=None, hit_rates=None, day=None) -> str:
    text, _ = render(day_str, ledger, account, hit_rates, day_state=day)
    path = config.REPORT_DIR / f"{day_str}.md"
    path.write_text(text)
    return str(path)
