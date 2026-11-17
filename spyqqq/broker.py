"""broker.py — the only module that talks to Alpaca.

Rules this module exists to enforce (each one a 2026-11-14 finding):
  * connect() retries forever with capped backoff — a DNS blip at boot is a
    delay, never a silently unauthenticated day.
  * paper only: PAPER_ONLY=True in config refuses live credentials outright.
  * spreads go out as ONE atomic MLEG limit order; a rejected leg can't leave
    a naked short.
  * every call that can raise is wrapped; callers get None/[] and a log line.
"""
from __future__ import annotations

import logging
import random
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from . import config
from .credentials import load_alpaca_creds
from .security import validate_api_key, validate_api_secret

log = logging.getLogger(__name__)


@dataclass
class Quote:
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0 if self.bid and self.ask else 0.0


@dataclass
class Contract:
    occ: str
    strike: float
    expiry: date
    opt_type: str          # call | put
    oi: Optional[int]
    bid: float = 0.0
    ask: float = 0.0
    iv: Optional[float] = None
    delta: Optional[float] = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0 if self.bid and self.ask else 0.0


class Broker:
    def __init__(self):
        self.trading = None
        self.stock_data = None
        self.option_data = None
        self.key_prefix = ""
        self.connected_at: Optional[datetime] = None

    # ── connection ───────────────────────────────────────────────────────────
    def connect(self, forever: bool = True, max_attempts: int = 0) -> bool:
        """Retry with jittered backoff (5s → 60s). Returns True once get_account() works."""
        creds = load_alpaca_creds()
        if not creds.is_complete:
            raise RuntimeError("No Alpaca credentials in .env (ALPACA_API_KEY / ALPACA_API_SECRET)")
        if config.PAPER_ONLY and not creds.paper:
            raise RuntimeError("PAPER_ONLY=True but .env says live — refusing to start")
        key, secret = validate_api_key(creds.key), validate_api_secret(creds.secret)
        self.key_prefix = creds.key_prefix
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.data.historical.option import OptionHistoricalDataClient
        attempt, delay = 0, 5.0
        while True:
            attempt += 1
            try:
                self.trading = TradingClient(key, secret, paper=True)
                self.stock_data = StockHistoricalDataClient(key, secret)
                self.option_data = OptionHistoricalDataClient(key, secret)
                acct = self.trading.get_account()
                self.connected_at = datetime.now()
                log.info(f"[broker] connected PAPER key={self.key_prefix}… "
                         f"equity=${float(acct.equity):,.2f} (attempt {attempt})")
                return True
            except Exception as e:
                self.trading = self.stock_data = self.option_data = None
                log.warning(f"[broker] connect attempt {attempt} failed: {type(e).__name__}: "
                            f"{str(e)[:160]} — retry in {delay:.0f}s")
                if not forever and max_attempts and attempt >= max_attempts:
                    return False
                _time.sleep(delay + random.uniform(0, 2))
                delay = min(delay * 2, 60.0)

    def alive(self) -> bool:
        try:
            self.trading.get_clock()
            return True
        except Exception:
            return False

    # ── account / clock ──────────────────────────────────────────────────────
    def account(self) -> Optional[dict]:
        try:
            a = self.trading.get_account()
            return {"equity": float(a.equity), "cash": float(a.cash),
                    "buying_power": float(a.buying_power),
                    "options_bp": float(getattr(a, "options_buying_power", 0) or 0)}
        except Exception as e:
            log.warning(f"[broker] account: {e}"); return None

    def clock(self) -> Optional[dict]:
        try:
            c = self.trading.get_clock()
            return {"is_open": bool(c.is_open), "next_open": c.next_open, "next_close": c.next_close,
                    "now": c.timestamp}
        except Exception as e:
            log.warning(f"[broker] clock: {e}"); return None

    # ── market data ──────────────────────────────────────────────────────────
    def daily_bars(self, sym: str, days: int = 260) -> list[dict]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        try:
            req = StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Day,
                                   start=datetime.utcnow() - timedelta(days=int(days * 1.6)),
                                   limit=days)
            bars = self.stock_data.get_stock_bars(req)[sym]
            return [{"ts": b.timestamp, "open": b.open, "high": b.high, "low": b.low,
                     "close": b.close, "volume": b.volume} for b in bars]
        except Exception as e:
            log.warning(f"[broker] daily_bars {sym}: {e}"); return []

    def intraday_bars(self, sym: str, start: datetime, end: Optional[datetime] = None,
                      minutes: int = 5) -> list[dict]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        try:
            req = StockBarsRequest(symbol_or_symbols=sym,
                                   timeframe=TimeFrame(minutes, TimeFrameUnit.Minute),
                                   start=start, end=end, limit=10000)
            data = self.stock_data.get_stock_bars(req)
            bars = data[sym] if sym in data.data else []
            return [{"ts": b.timestamp, "open": b.open, "high": b.high, "low": b.low,
                     "close": b.close, "volume": b.volume} for b in bars]
        except Exception as e:
            log.warning(f"[broker] intraday_bars {sym}: {e}"); return []

    def chain(self, sym: str, opt_type: str, exp_lo: date, exp_hi: date,
              strike_lo: float, strike_hi: float) -> list[Contract]:
        """Contracts (OI from the trading API) merged with live quotes/IV/greeks (data API)."""
        from alpaca.trading.requests import GetOptionContractsRequest
        from alpaca.trading.enums import ContractType as TCT
        from alpaca.data.requests import OptionChainRequest
        DCT = TCT  # alpaca-py 0.45: ContractType lives only in alpaca.trading.enums
        out: dict[str, Contract] = {}
        try:
            req = GetOptionContractsRequest(underlying_symbols=[sym],
                                            type=TCT.CALL if opt_type == "call" else TCT.PUT,
                                            expiration_date_gte=exp_lo, expiration_date_lte=exp_hi,
                                            strike_price_gte=f"{strike_lo:g}", strike_price_lte=f"{strike_hi:g}",
                                            limit=500)
            resp = self.trading.get_option_contracts(req)
            for c in (resp.option_contracts or []):
                out[c.symbol] = Contract(occ=c.symbol, strike=float(c.strike_price),
                                         expiry=c.expiration_date, opt_type=opt_type,
                                         oi=int(c.open_interest) if c.open_interest else None)
        except Exception as e:
            log.warning(f"[broker] contracts {sym}: {e}"); return []
        try:
            creq = OptionChainRequest(underlying_symbol=sym,
                                      type=DCT.CALL if opt_type == "call" else DCT.PUT,
                                      expiration_date_gte=exp_lo, expiration_date_lte=exp_hi,
                                      strike_price_gte=strike_lo, strike_price_lte=strike_hi)
            snaps = self.option_data.get_option_chain(creq)
            for occ, s in snaps.items():
                if occ not in out:
                    continue
                q = s.latest_quote
                if q is not None:
                    out[occ].bid, out[occ].ask = float(q.bid_price or 0), float(q.ask_price or 0)
                out[occ].iv = float(s.implied_volatility) if s.implied_volatility else None
                out[occ].delta = float(s.greeks.delta) if s.greeks and s.greeks.delta is not None else None
        except Exception as e:
            log.warning(f"[broker] chain snapshots {sym}: {e}")
        return sorted(out.values(), key=lambda c: (c.expiry, c.strike))

    def option_quote(self, occ: str) -> Optional[Quote]:
        from alpaca.data.requests import OptionLatestQuoteRequest
        try:
            q = self.option_data.get_option_latest_quote(OptionLatestQuoteRequest(symbol_or_symbols=occ))[occ]
            return Quote(float(q.bid_price or 0), float(q.ask_price or 0))
        except Exception as e:
            log.warning(f"[broker] quote {occ}: {e}"); return None

    def spread_mid(self, long_occ: str, short_occ: str) -> Optional[float]:
        lq, sq = self.option_quote(long_occ), self.option_quote(short_occ)
        if not lq or not sq or lq.mid <= 0:
            return None
        return max(lq.mid - sq.mid, 0.01)

    def latest_price(self, sym: str) -> Optional[float]:
        from alpaca.data.requests import StockLatestTradeRequest
        try:
            t = self.stock_data.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=sym))[sym]
            return float(t.price)
        except Exception as e:
            log.warning(f"[broker] latest_price {sym}: {e}"); return None

    # ── orders ───────────────────────────────────────────────────────────────
    def open_spread(self, long_occ: str, short_occ: str, qty: int, net_limit: float) -> Optional[str]:
        from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        legs = [OptionLegRequest(symbol=long_occ, ratio_qty=1, side=OrderSide.BUY),
                OptionLegRequest(symbol=short_occ, ratio_qty=1, side=OrderSide.SELL)]
        req = LimitOrderRequest(qty=qty, order_class=OrderClass.MLEG, legs=legs,
                                limit_price=round(net_limit, 2), time_in_force=TimeInForce.DAY)
        try:
            o = self.trading.submit_order(req)
            log.info(f"[broker] MLEG open {long_occ}/{short_occ} x{qty} @ ≤{net_limit:.2f} id={o.id}")
            return str(o.id)
        except Exception as e:
            log.error(f"[broker] open_spread failed: {e}"); return None

    def close_spread(self, long_occ: str, short_occ: str, qty: int, net_limit: float) -> Optional[str]:
        """Sell the long, buy back the short, atomically, at a net CREDIT limit."""
        from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        legs = [OptionLegRequest(symbol=long_occ, ratio_qty=1, side=OrderSide.SELL),
                OptionLegRequest(symbol=short_occ, ratio_qty=1, side=OrderSide.BUY)]
        req = LimitOrderRequest(qty=qty, order_class=OrderClass.MLEG, legs=legs,
                                limit_price=round(max(net_limit, 0.01), 2), time_in_force=TimeInForce.DAY)
        try:
            o = self.trading.submit_order(req)
            log.info(f"[broker] MLEG close {long_occ}/{short_occ} x{qty} @ ≥{net_limit:.2f} id={o.id}")
            return str(o.id)
        except Exception as e:
            log.error(f"[broker] close_spread failed: {e}"); return None

    def wait_fill(self, order_id: str, timeout: int = config.FILL_TIMEOUT_SEC) -> dict:
        """{'status': filled|partial|rejected|pending, 'qty': int, 'price': float|None}"""
        deadline = _time.time() + timeout
        last = {"status": "pending", "qty": 0, "price": None, "raw": "unknown"}
        while _time.time() < deadline:
            try:
                o = self.trading.get_order_by_id(order_id)
                raw = str(o.status).lower().split(".")[-1]
                cat = ("filled" if raw in ("filled", "done_for_day") else
                       "partial" if raw == "partially_filled" else
                       "rejected" if raw in ("canceled", "cancelled", "expired", "rejected", "suspended")
                       else "pending")
                last = {"status": cat, "qty": int(o.filled_qty or 0),
                        "price": float(o.filled_avg_price) if o.filled_avg_price else None, "raw": raw}
                if cat in ("filled", "rejected"):
                    return last
            except Exception as e:
                log.warning(f"[broker] wait_fill {order_id}: {e}")
            _time.sleep(2.0)
        return last

    def cancel(self, order_id: str) -> None:
        try:
            self.trading.cancel_order_by_id(order_id)
        except Exception as e:
            log.warning(f"[broker] cancel {order_id}: {e}")

    def positions(self) -> list[dict]:
        try:
            return [{"symbol": p.symbol, "qty": float(p.qty), "avg_entry": float(p.avg_entry_price),
                     "market_value": float(p.market_value or 0), "unrealized": float(p.unrealized_pl or 0),
                     "asset_class": str(p.asset_class).split(".")[-1].lower()}
                    for p in self.trading.get_all_positions()]
        except Exception as e:
            log.warning(f"[broker] positions: {e}"); return []

    def close_position_market(self, symbol: str) -> bool:
        """Last resort for an orphan single leg — market close."""
        try:
            self.trading.close_position(symbol)
            return True
        except Exception as e:
            log.error(f"[broker] close_position {symbol}: {e}"); return False
