"""Stock price bars, behind a small interface so the data source can be swapped."""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Protocol, Tuple

Bars = List[Tuple[datetime, float]]  # (bar start, open), tz-aware, ascending
Closes = Dict[date, float]  # exchange-local trading date -> close


class PriceSource(Protocol):
    def intraday_opens(self, ticker: str, start: datetime) -> Bars:
        """Regular-session 5-minute bars since `start` (at most ~60 days back)."""

    def daily_closes(self, ticker: str, start: date) -> Closes:
        """Daily closes since `start`, split-adjusted but not dividend-adjusted."""


class YahooPrices:
    """yfinance: free and unofficial, so callers treat failures as per-ticker and retryable."""

    def intraday_opens(self, ticker: str, start: datetime) -> Bars:
        import yfinance as yf  # imported lazily: only the label job needs pandas

        df = yf.Ticker(ticker).history(interval="5m", start=start, prepost=False, auto_adjust=False)
        return [(ts.to_pydatetime(), float(o)) for ts, o in df["Open"].dropna().items()]

    def daily_closes(self, ticker: str, start: date) -> Closes:
        import yfinance as yf

        df = yf.Ticker(ticker).history(interval="1d", start=start, auto_adjust=False)
        return {ts.date(): float(c) for ts, c in df["Close"].dropna().items()}
