"""ChainSource backed by yfinance (free, unofficial; callers treat failures as retryable)."""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import List, Optional

from . import Chain, OptionQuote


def _num(x) -> Optional[float]:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _int(x) -> Optional[int]:
    x = _num(x)
    return None if x is None else int(x)


class YahooChains:
    def expiries(self, ticker: str) -> List[date]:
        import yfinance as yf

        return sorted(date.fromisoformat(e) for e in yf.Ticker(ticker).options)

    def chain(self, ticker: str, expiry: date) -> Chain:
        import yfinance as yf

        oc = yf.Ticker(ticker).option_chain(expiry.isoformat())
        quotes = []
        for right, df in (("C", oc.calls), ("P", oc.puts)):
            for r in df.to_dict("records"):
                quotes.append(
                    OptionQuote(
                        contract_symbol=r["contractSymbol"],
                        expiry=expiry,
                        strike=float(r["strike"]),
                        right=right,
                        bid=_num(r.get("bid")),
                        ask=_num(r.get("ask")),
                        last=_num(r.get("lastPrice")),
                        iv=_num(r.get("impliedVolatility")),
                        volume=_int(r.get("volume")),
                        open_interest=_int(r.get("openInterest")),
                    )
                )
        u = oc.underlying or {}
        return Chain(
            ticker=ticker,
            expiry=expiry,
            underlying_price=float(u["regularMarketPrice"]),
            quoted_at=datetime.fromtimestamp(u["regularMarketTime"], tz=timezone.utc),
            quotes=quotes,
        )
