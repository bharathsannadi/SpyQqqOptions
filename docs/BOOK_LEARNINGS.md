# Book learnings — targeted re-read for the SPY/QQQ debit-spread bot (2026-11-15)

The knowledge base (`docs/knowledge_base.md`, ~50 books deeply read May–June 2026) already
carries the general rules. This pass re-read the books that speak *directly* to what this bot
does — SPY/QQQ, intraday, vertical debit spreads, 7–21 DTE, VWAP, time of day, sizing, exits —
and asked one question of each: **does the current rule set miss something this author
measured or insisted on?** Text was extracted from 15 titles in
`/Users/bsannadi/Desktop/bharath/books/Trading/` (two were image-only scans and unreadable:
Passarelli *Trading Option Greeks*, Lukeman *The Market Maker's Edge*).

Status key: **applied** (in code today) · **already** (the rule set had it) · **research**
(needs a measurement before it becomes a rule) · **note** (recorded, no action).

## Applied today

| # | Learning | Source | What changed |
|---|---|---|---|
| BK-1 | **Choose the vertical's legs by implied volatility.** "If implied volatility is low, the choice of spreads should focus on *purchasing* the at-the-money option. If implied volatility is high, the choice should focus on *selling* the at-the-money option." The spread that includes the nearer-the-money long always costs more, and that cost buys the case where the market goes nowhere. | Natenberg, *Option Volatility and Pricing* 2nd ed., ch. 12 "Bull and Bear Spreads" | Our §22 gate admits a trade only when realised vol exceeds implied — Natenberg's "IV low" case. `LONG_OFFSETS` was `(2, 3, 1)` (2–3 strikes OTM first, chosen so the debit lands in §5's 25–45% band). Now `(1, 2, 3)`: the long nearest the money that still satisfies §5 wins. §5 is unchanged, so an ATM long on a 5-wide (52–54% of width) is still refused. |
| BK-3 | **10:00 ET is a scheduled-release slot and the first half hour is a different market.** Business time runs 5.3× physical time between 09:30 and 10:00, then decays; 12:00–14:00 is the slowest. ISM, JOLTS, consumer sentiment and new-home sales print at 10:00. | *Stock Market Volatility* (2009), business-time chapter; Cofnas on release timing (KB §17) | First scan moved from 10:00 to **10:05** so the 10:00–10:05 bar, which absorbs a release, is closed before any entry. The confidence replay uses the same window, so hit rates recompute at boot. |
| BK-4 | **Triple witching is a different day, and for SPY/QQQ it is also ex-dividend.** Third Friday of Mar/Jun/Sep/Dec: index futures, index options and equity options all expire ("extraordinary volatility"). Smith: equity/ETF options are American-style; a short call in the money ahead of an ex-dividend date is the classic early-assignment trap, which "can remove the very protection that you wanted." | *Stock Trader's Almanac 2021* ("Aura of the Triple Witch"); Smith, *Complete Guide to Option Strategies* ch. 28 | `WITCHING_BLACKOUT = True` → `gates.event_blackout` refuses new entries on those four Fridays, alongside FOMC (operator list) and first-Friday NFP. |
| BK-6 | **A rolling loss limit, not just a daily one.** Elder: lose 6% of equity in a month → stop trading for the month. Davey: a system whose live return/drawdown falls under 2.0 "is taking on too much risk for the reward" — and the decision to stop must be set *before* the drawdown. | Elder, *Come Into My Trading Room* / *New Trading for a Living* (KB §RM); Davey, *Building Winning Algorithmic Trading Systems* ch. 14, 19 | New gate `§RM drawdown`: net realised over the last 10 sessions ≤ −$3,000 (two daily budgets) → no new entries until reviewed. Exits keep running. Shown as a chip on the dashboard. |

## Already enforced (the books agree with what was built)

| # | Learning | Source | Where |
|---|---|---|---|
| BK-2 | Don't enter in the first 30–60 minutes; "weak hands make emotional, uninformed decisions in the first hour"; a first-hour move often reverses and tests the prior day's high/low; a strong morning move closes higher only ~50% of the time → expect **midday give-backs**. | Connors & Raschke, *Street Smarts* (first-hour breakout, Taylor's observation, Smart Money Index) | Window 10:05–11:30; TP +20% taken immediately; 90-min stall exit; ratchet locks gains. The give-back statistic is the argument for the stall exit, keep it. |
| BK-5 | Monday and Friday are the weak days; traders square positions Friday and reprice Monday. | *Stock Trader's Almanac* (daily performance since 1952; "Down Friday / Down Monday") | Not a gate (intraday first-touch hit rates by weekday were not measurably different in the 1,372-session replay — see research item R-2 to re-check). |
| BK-7 | "Good traders have their exits planned before they enter": what if the target is reached early, what if direction is wrong, what if the move stalls. | Saliba, *Option Spread Strategies* ch. 2–3 | `exits.check` order: flatten → TP → stall → ratchet, all fixed before entry. |
| BK-8 | Weeklies made spreads penny-wide; "stay with liquidity — less-liquid options have wider bid/ask and are harder to execute"; vertical spreads are the preferred short-duration vehicle. | Keene, *Keene on the Market* | §9 liquidity (OI ≥ 200, two-sided quotes) and §9 spread_cost (both legs' width ≤ 10% of debit); atomic MLEG limit orders only. |
| BK-9 | Time decay is fastest in the last 30 days and for ATM options; option *buyers* should avoid the front month. | Cohen, *Volatile Markets Made Easy* ch. 3 | Partly: DTE 7–21 is a deliberate trade-off — intraday holding makes theta per hour small, while nearer expiries give the vertical more delta (Saliba fig. 2.9–2.16). Research item R-1 measures it. |
| BK-10 | Incubate a finished system 3–6 months without money, check monthly, parameters frozen; the emotional high after a good backtest is when you are most likely to size up badly. | Davey ch. 14 | `PAPER_ONLY = True`, no live path; one constant per day after reading the report (RUNBOOK). The paper phase *is* the incubation — do not shorten it. |
| BK-11 | VIX ÷ 16 ≈ the expected one-day move of the S&P in percent; VIX and SPX move inversely on ~84% of days. | Rhoads, *Trading VIX Derivatives* ch. 2, 6 | We have no VIX feed; `data.iv30` from the SPY chain is the same quantity for SPY. Research item R-3 uses it. |
| BK-12 | Session VWAP's direction is the intraday trend; in trending conditions stay on one side of it, in ranges trade both sides. The prior day's closing VWAP is a strong pivot for today. | Coles & Hawkins, *MIDAS Technical Analysis* ch. 1 (Steenbarger, English) | `vwap_momentum` requires price on the VWAP side with >50% of recent bars there. Prior-day VWAP as a filter is research item R-4. |
| BK-13 | A fund's published system criteria: "no overnight exposure, ≤2 indicators, 100% mechanical; dislikes: more than 2 trades per day, adding to losers." | Davey ch. 9 (sample investor criteria) | We flatten daily and never add to a position. Today's raise to 3 trades/day sits just outside that criterion — worth remembering when reading the first weeks' reports. |

## Research items (measure before ruling)

| # | Question | How to measure | Why it matters |
|---|---|---|---|
| R-1 | Does 21–45 DTE beat 7–21 DTE on realised spread P&L for a same-day hold? | Needs intraday option bars (the S2 harness gap in LEARNINGS "Still open"). Until then: log the candidate's DTE on every fill and split the rolling stats by DTE bucket in the report. | Cohen says avoid the front month; Saliba's Greek curves say nearer expiries move more per point of underlying. Only the ledger can settle it. |
| R-2 | Weekday effect on the first-touch hit rate (Mon/Fri vs mid-week). | `history.replay` already returns per-session outcomes; group by weekday and print Wilson intervals in `research.py`. | Almanac's Monday/Friday weakness is a daily-close effect; it may not survive at the 30–60 min horizon. |
| R-3 | Should the ±0.30% first-touch threshold scale with expected move (IV30/16 per day → per 90 minutes)? | Re-run the replay with threshold = k × expected move; compare hit rate stability across the 2021–2026 vol regimes. | A fixed 0.30% is a different bet at VIX 12 than at VIX 30. |
| R-4 | Does requiring price on the same side of the *prior session's* VWAP raise the vwap_momentum lower bound above 50% for QQQ or for bears? | Add an optional filter in `signal.evaluate_vwap_momentum`, replay 1,372 sessions, compare intervals. | Today only SPY bulls clear 50%; a context filter is the cheapest way to find a second admissible cell. |
| R-5 | Prior-day NR7 / range contraction → trend day (Crabel, via Connors & Raschke). Is the hit rate higher after a narrow-range day? | Daily bars are cached; tag sessions whose prior day was the narrowest of 7 and split the replay. | Trend days are when a directional debit spread earns its debit. |

## Notes (no action)

- Rhoads: VIX futures under-react to VIX index spikes; irrelevant to us (no VIX positions) but explains why "VIX up 4 points" headlines do not map to SPY option prices one-for-one.
- Smith ch. 28: cash-settled, European-style SPX/NDX options would remove assignment risk entirely. Alpaca lists SPY/QQQ only; we are flat before the close, so the risk is bounded to a broker outage between 15:45 and 16:00 — the stale-quote and reconcile paths exist for that.
- Natenberg ch. 12 on why a trader prefers a vertical to the underlying: limited risk per unit of delta. That is the whole thesis of this bot; the ±20% on net debit is the only part of the exit that is *not* in the books, it is the operator's rule.
