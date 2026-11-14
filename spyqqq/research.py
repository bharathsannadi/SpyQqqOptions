"""research.py — offline parameter sweep of the entry rule on the cached history.

    venv/bin/python -m spyqqq.research            # full grid → reports/research-<date>.md

No network: reads state/bars/*.jsonl only. Everything is measured with the same
signal.evaluate / history.replay the live engine uses, so what you see here is
what the dashboard's confidence number would have been. Treat the best cell of a
grid as optimistic (KB §12): prefer parameters that are good across years.
"""
from __future__ import annotations

import itertools
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from . import config, history, signal as sig
from .data import _bar

ET_FMT = "%Y-%m-%d"


def load(sym: str):
    intraday: dict[date, list[sig.Bar]] = defaultdict(list)
    seen = set()
    for line in (config.BARS_CACHE_DIR / f"{sym}_5m.jsonl").read_text().splitlines():
        d = json.loads(line)
        if d["ts"] in seen:
            continue
        seen.add(d["ts"]); d["ts"] = datetime.fromisoformat(d["ts"])
        b = _bar(d)
        if "09:30" <= b.ts.strftime("%H:%M") <= "16:00":
            intraday[b.ts.date()].append(b)
    intraday = {d: sorted(v, key=lambda x: x.ts) for d, v in intraday.items() if len(v) >= 30}
    daily_rows = [json.loads(l) for l in (config.BARS_CACHE_DIR / f"{sym}_1d.jsonl").read_text().splitlines()]
    closes = [(datetime.strptime(r["ts"][:10], ET_FMT).date(), float(r["close"])) for r in daily_rows]
    closes.sort()
    daily_by_date = {}
    for d in intraday:
        daily_by_date[d] = [c for (dd, c) in closes if dd < d]
    return daily_by_date, intraday


def run_cell(sym, daily, intraday, rsi, vol, tgt, stp, years=None, setup="trend_pullback"):
    config.RSI_MIN, config.RSI_MAX = rsi
    config.VOL_RATIO_MIN = vol
    history.HIST_TARGET_PCT, history.HIST_STOP_PCT = tgt, stp
    if years:
        intraday = {d: v for d, v in intraday.items() if d.year in years}
    return history.replay(sym, daily, intraday, setup)


def main() -> None:
    grid_rsi = [(40, 65), (35, 70), (45, 65)]
    grid_vol = [0.8, 1.0, 1.2]
    grid_out = [(0.0030, 0.0030), (0.0040, 0.0030), (0.0050, 0.0050)]
    lines = [f"# Entry-rule sweep — {datetime.now():%Y-%m-%d %H:%M}", ""]
    quick = "--quick" in sys.argv          # vwap_momentum + by-year only
    for sym in config.UNDERLYINGS:
        daily, intraday = load(sym)
        yrs = sorted({d.year for d in intraday})
        lines += [f"## {sym} — vwap_momentum (ported from AlpacaTrader) — {len(intraday)} sessions", "",
                  "| tgt/stp | dir | n | hit | lower95 | avg move % |", "|---|---|---|---|---|---|"]
        for (tgt, stp) in grid_out:
            hr = run_cell(sym, daily, intraday, (40, 65), 0.8, tgt, stp, setup="vwap_momentum")
            for d in ("bull", "bear"):
                h = hr[d]
                lines.append(f"| {tgt*100:.2f}/{stp*100:.2f} | {d} | {h.n} | {(h.rate or 0):.0%} | "
                             f"{(h.lower95 or 0):.0%} | {(h.avg_move_pct or 0):+.3f} |")
            print(f"{sym} vwap_momentum out={tgt}/{stp}: bull n={hr['bull'].n} {hr['bull'].rate} bear n={hr['bear'].n} {hr['bear'].rate}", flush=True)
        lines += ["", f"### {sym} vwap_momentum by year (0.30/0.30)", "", "| year | bull n | bull hit | bear n | bear hit |", "|---|---|---|---|---|"]
        for y in yrs:
            hr = run_cell(sym, daily, intraday, (40, 65), 0.8, 0.0030, 0.0030, years={y}, setup="vwap_momentum")
            lines.append(f"| {y} | {hr['bull'].n} | {(hr['bull'].rate or 0):.0%} | {hr['bear'].n} | {(hr['bear'].rate or 0):.0%} |")
        lines.append("")
        if quick:
            continue
        lines += [f"## {sym} — {len(intraday)} sessions {min(intraday)} → {max(intraday)}", "",
                  "| RSI | vol≥ | tgt/stp | dir | n | hit | lower95 | avg move % |", "|---|---|---|---|---|---|---|---|"]
        for rsi, vol, (tgt, stp) in itertools.product(grid_rsi, grid_vol, grid_out):
            hr = run_cell(sym, daily, intraday, rsi, vol, tgt, stp)
            for d in ("bull", "bear"):
                h = hr[d]
                lines.append(f"| {rsi} | {vol} | {tgt*100:.2f}/{stp*100:.2f} | {d} | {h.n} | "
                             f"{(h.rate or 0):.0%} | {(h.lower95 or 0):.0%} | {(h.avg_move_pct or 0):+.3f} |")
            print(f"{sym} rsi={rsi} vol={vol} out={tgt}/{stp}: bull n={hr['bull'].n} {hr['bull'].rate} bear n={hr['bear'].n} {hr['bear'].rate}", flush=True)
        # stability by year at the default cell
        lines += ["", f"### {sym} by year (RSI 40,65, vol≥0.8, 0.30/0.30)", "",
                  "| year | bull n | bull hit | bear n | bear hit |", "|---|---|---|---|---|"]
        for y in yrs:
            hr = run_cell(sym, daily, intraday, (40, 65), 0.8, 0.0030, 0.0030, years={y})
            lines.append(f"| {y} | {hr['bull'].n} | {(hr['bull'].rate or 0):.0%} | {hr['bear'].n} | {(hr['bear'].rate or 0):.0%} |")
        lines.append("")
    out = config.REPORT_DIR / f"research-{datetime.now():%Y-%m-%d}.md"
    out.write_text("\n".join(lines))
    print(f"\n→ {out}")


if __name__ == "__main__":
    main()
