"""debate.py — bull/bear/judge LLM gate, carried from AlpacaTrader.

Kept: the three-call structure, the KB preamble, and the failure semantics
(gate OFF → fail open with conf 1.0; gate ON but broke → fail CLOSED). Changed:
model via llm.py (Opus 5.5), KB sections read from docs/knowledge_base.md,
and the setup text is built from this bot's Signal.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from . import config, llm
from .signal import Signal

log = logging.getLogger(__name__)

_SECTIONS = ("## 2. IV & Volatility Rules", "## 3. Entry & Exit Timing",
             "## 7. Common Mistakes", "## 8. Key Rules from the Masters",
             "## 9. Checklist Before Every Trade", "## 22. Variance Risk Premium")


def _load_kb_rules(max_lines: int = 220) -> str:
    if not config.KB_PATH.exists():
        return ""
    out, capture = [], False
    for line in config.KB_PATH.read_text().splitlines():
        if line.startswith("## "):
            capture = any(line.startswith(h) for h in _SECTIONS)
        if capture:
            out.append(line)
    return "\n".join(out[:max_lines])


_PREAMBLE = (
    "You are gating trades for a PAPER-money bot that buys SPY/QQQ vertical debit "
    "spreads with 7–21 DTE, exits at ±20% of net debit, and flattens by 15:45 ET. "
    "Apply these rules strictly:\n"
    "• Buy premium only when forecast realised vol exceeds implied (KB §22) — never pay the VRP blind\n"
    "• No entries before 10:00 ET or after 11:30 ET; theta on a day trade is the enemy\n"
    "• No chasing: RSI14 outside 40–65 (bull) / 35–60 (bear) is a reject\n"
    "• Max loss must be stated before entry; it is the net debit — if that is not acceptable, reject\n"
    "• After a >2% down day, do not buy puts — IV is already inflated (Thomsett)\n"
    "• No entries on FOMC / CPI / NFP release mornings (Cofnas)\n"
    "• Limit orders only; a wide bid-ask is a reject (KB §9)\n\n"
) + _load_kb_rules()

_BULL = ("You are a bullish options trader. Make the strongest bull case for the setup in 3–4 "
         "sentences, citing Greeks and IV. Flag what weakens it. End with: Confidence: X%.\n\n" + _PREAMBLE)
_BEAR = ("You are a bearish options trader. Make the strongest bear case for the setup in 3–4 "
         "sentences (theta, IV crush, overextension). End with: Confidence: X%.\n\n" + _PREAMBLE)
_JUDGE = ("You are a neutral trading judge. Given the bull and bear arguments, decide whether to "
          "proceed with the proposed direction. Respond with exactly this JSON and nothing else:\n"
          '{"proceed": true/false, "confidence": 0.00, "reason": "one sentence"}\n'
          f"Only set proceed=true if confidence >= {config.DEBATE_MIN_CONFIDENCE:.2f}.\n\n" + _PREAMBLE)


def _setup_text(s: Signal, extra: dict) -> str:
    i = s.indicators
    lines = [f"Symbol: {s.sym} | Proposed: {s.direction.upper()} debit spread | Setup: {s.setup}",
             f"Price {s.price:.2f}  EMA9 {i.get('ema9')}  RSI14 {i.get('rsi14')}  "
             f"VolRatio {i.get('vol_ratio')}  DailyTrend {i.get('trend')}  Strength {s.strength}",
             f"HV5 {s.hv5}  HV30 {s.hv30}  IV30 {s.iv30}  IVR {s.ivr}"]
    for k, v in extra.items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines)


def _parse_judge(raw: str) -> tuple[bool, float, str]:
    try:
        clean = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
        m = re.search(r"\{.*\}", clean, re.DOTALL)
        data = json.loads(m.group(0) if m else clean)
        conf = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        return bool(data.get("proceed", False)), conf, str(data.get("reason", ""))
    except Exception:
        log.warning(f"debate: unparseable judge response: {raw[:160]!r} — failing CLOSED")
        return False, 0.0, "judge_parse_failed"


def run(sig: Signal, extra: Optional[dict] = None) -> dict:
    """Returns {"proceed", "confidence", "reason", "bull", "bear", "status"}.

    status: off | ok | failed_closed."""
    extra = extra or {}
    if not config.DEBATE_ENABLED or llm.client() is None:
        return {"proceed": True, "confidence": 1.0, "reason": "debate gate OFF",
                "bull": "", "bear": "", "status": "off"}
    setup = _setup_text(sig, extra)
    bull = llm.ask(_BULL, f"Setup:\n{setup}\n\nMake the bull case.", config.DEBATE_MODEL, 400)
    if bull is None:
        return {"proceed": False, "confidence": 0.0, "reason": f"bull call failed ({llm.last_error}) — gate fails CLOSED",
                "bull": "", "bear": "", "status": "failed_closed"}
    bear = llm.ask(_BEAR, f"Setup:\n{setup}\n\nMake the bear case.", config.DEBATE_MODEL, 400)
    if bear is None:
        return {"proceed": False, "confidence": 0.0, "reason": f"bear call failed ({llm.last_error}) — gate fails CLOSED",
                "bull": bull, "bear": "", "status": "failed_closed"}
    judge = llm.ask(_JUDGE, f"Setup:\n{setup}\n\nBULL: {bull}\n\nBEAR: {bear}\n\n"
                            f"Proposed direction: {sig.direction.upper()}. Proceed?",
                    config.DEBATE_MODEL, 200)
    if judge is None:
        return {"proceed": False, "confidence": 0.0, "reason": f"judge call failed ({llm.last_error}) — gate fails CLOSED",
                "bull": bull, "bear": bear, "status": "failed_closed"}
    proceed, conf, reason = _parse_judge(judge)
    proceed = proceed and conf >= config.DEBATE_MIN_CONFIDENCE
    return {"proceed": proceed, "confidence": conf, "reason": reason,
            "bull": bull, "bear": bear, "status": "ok"}
