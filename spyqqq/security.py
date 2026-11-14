"""security.py — input validators carried from AlpacaTrader (the web/login parts dropped)."""
from __future__ import annotations

import re

_KEY_RE    = re.compile(r"^[A-Za-z0-9]{16,64}$")
_SECRET_RE = re.compile(r"^[A-Za-z0-9/+=_\-]{32,128}$")


def validate_api_key(value: str) -> str:
    v = (value or "").strip()
    if not _KEY_RE.match(v):
        raise ValueError("API key must be 16–64 alphanumeric characters")
    return v


def validate_api_secret(value: str) -> str:
    v = (value or "").strip()
    if not _SECRET_RE.match(v):
        raise ValueError("API secret must be 32–128 characters")
    return v
