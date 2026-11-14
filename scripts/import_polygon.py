#!/usr/bin/env python
"""One-off: seed state/bars/ from the archived Polygon cache (2021-06 → 2026-05).

  SPY_minute.parquet / QQQ_minute.parquet  → state/bars/{SYM}_5m.jsonl  (resampled)
  SPY_daily.parquet  / QQQ_daily.parquet   → state/bars/{SYM}_1d.jsonl

Rows already present (from the Alpaca cache) are kept; everything is de-duplicated
by timestamp. Safe to re-run. Needs pyarrow (requirements-dev).

    venv/bin/python scripts/import_polygon.py [--cache /path/to/polygon_cache]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from spyqqq import config  # noqa: E402

DEFAULT_CACHE = Path("/Users/bsannadi/Desktop/bharath/AlpacaTrader_Data/polygon_cache")


def _load_jsonl(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                d = json.loads(line); out[d["ts"]] = d
            except Exception:
                pass
    return out


def _write_jsonl(path: Path, rows: dict[str, dict]) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        for ts in sorted(rows):
            f.write(json.dumps(rows[ts]) + "\n")
    tmp.replace(path)


def import_minutes(sym: str, cache: Path) -> tuple[int, int]:
    df = pd.read_parquet(cache / f"{sym}_minute.parquet")
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("America/New_York")
    df = df.set_index("ts").sort_index()
    df = df.between_time("09:30", "15:59")                       # regular session only
    five = df.resample("5min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    five = five.between_time("09:30", "15:55")
    path = config.BARS_CACHE_DIR / f"{sym}_5m.jsonl"
    rows = _load_jsonl(path); before = len(rows)
    for ts, r in five.iterrows():
        key = ts.isoformat()
        if key not in rows:
            rows[key] = {"ts": key, "open": float(r.open), "high": float(r.high), "low": float(r.low),
                         "close": float(r.close), "volume": float(r.volume)}
    _write_jsonl(path, rows)
    return before, len(rows)


def import_daily(sym: str, cache: Path) -> tuple[int, int]:
    df = pd.read_parquet(cache / f"{sym}_daily.parquet")
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("America/New_York")
    path = config.BARS_CACHE_DIR / f"{sym}_1d.jsonl"
    rows = _load_jsonl(path); before = len(rows)
    for _, r in df.iterrows():
        key = r.ts.strftime("%Y-%m-%d")
        if key not in rows:
            rows[key] = {"ts": key, "open": float(r.open), "high": float(r.high), "low": float(r.low),
                         "close": float(r.close), "volume": float(r.volume)}
    _write_jsonl(path, rows)
    return before, len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    a = ap.parse_args()
    for sym in config.UNDERLYINGS:
        b, n = import_minutes(sym, a.cache)
        print(f"{sym} 5m: {b} → {n} bars (+{n - b})")
        b, n = import_daily(sym, a.cache)
        print(f"{sym} 1d: {b} → {n} bars (+{n - b})")


if __name__ == "__main__":
    main()
