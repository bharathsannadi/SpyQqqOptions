"""llm.py — one place that talks to Claude.

Opus 5.5 by default (config.DEBATE_MODEL / REVIEW_MODEL). Server-side refusal
fallbacks are on so a safety decline re-routes inside the same call; if the
installed SDK or platform rejects the parameter we retry without it. Every
caller gets `None` on any failure — gates decide what None means (debate fails
CLOSED; the EOD review just prints "review unavailable").
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

_client = None
# why the last call returned None, in a few words ("" after a success) — so the report and
# the debate gate can say "API credit balance too low" instead of a generic "call failed"
last_error: str = ""


def _classify(e: Exception) -> str:
    m = str(e).lower()
    if "credit balance" in m:
        return "Anthropic API credit balance too low"
    if "authentication" in m or "api key" in m or "401" in m:
        return "Anthropic API key rejected"
    if "rate limit" in m or "429" in m:
        return "Anthropic API rate-limited"
    return f"{type(e).__name__}"


def client():
    global _client
    if _client is not None:
        return _client
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return None
    try:
        import anthropic
        _client = anthropic.Anthropic(max_retries=2, timeout=60.0)
    except Exception as e:                      # SDK missing / misconfigured
        log.warning(f"llm: client init failed: {e}")
        _client = None
    return _client


def ask(system: str, user: str, model: str, max_tokens: int = 1024,
        effort: str = "low") -> Optional[str]:
    """Single-turn completion. Returns the text, or None on refusal/error."""
    global last_error
    c = client()
    if c is None:
        last_error = "no ANTHROPIC_API_KEY"
        return None
    kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                  messages=[{"role": "user", "content": user}],
                  output_config={"effort": effort})
    try:
        try:
            resp = c.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                          fallbacks="default", **kwargs)
        except TypeError:                       # older SDK without `fallbacks`
            resp = c.messages.create(**kwargs)
        except Exception as e:
            if "fallback" in str(e).lower():    # platform rejects the param
                resp = c.messages.create(**kwargs)
            else:
                raise
    except Exception as e:
        last_error = _classify(e)
        # billing / auth never heal by themselves — ERROR so it stands out in the log
        lvl = log.error if last_error.startswith("Anthropic API c") or "rejected" in last_error else log.warning
        lvl(f"llm: {model} call failed ({last_error}): {type(e).__name__}: {e}")
        return None
    if getattr(resp, "stop_reason", None) == "refusal":
        log.warning("llm: request refused by safety classifier")
        last_error = "refused"
        return None
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
    last_error = "" if text.strip() else "empty response"
    return text.strip() or None


def probe(model: str) -> str:
    """One max_tokens=1 call at boot / each new session so an exhausted credit balance or a
    revoked key shows on the dashboard *before* the entry window — not on the first signal,
    where the debate gate would just fail closed. Returns last_error ("" = reachable)."""
    global last_error
    c = client()
    if c is None:
        last_error = "no ANTHROPIC_API_KEY"
        return last_error
    try:
        c.messages.create(model=model, max_tokens=1, messages=[{"role": "user", "content": "ok"}])
        last_error = ""
    except Exception as e:
        last_error = _classify(e)
        log.error(f"llm: probe {model} failed ({last_error}): {e}")
    return last_error
