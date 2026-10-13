"""Option chains and contract selection, behind an interface so the source can be swapped."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional, Protocol


@dataclass
class OptionQuote:
    contract_symbol: str
    expiry: date
    strike: float
    right: str  # "C" or "P"
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    iv: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None


@dataclass
class Chain:
    ticker: str
    expiry: date
    underlying_price: float
    quoted_at: datetime  # time of the underlying's last regular-session trade
    quotes: List[OptionQuote] = field(default_factory=list)


class ChainSource(Protocol):
    def expiries(self, ticker: str) -> List[date]:
        """Listed expiration dates, ascending."""

    def chain(self, ticker: str, expiry: date) -> Chain:
        """Calls and puts for one expiry."""


WEEKLY_MIN_DAYS = 2  # nearest expiry at least this many days out...
MONTHLY_TARGET_DAYS = 30  # ...plus the expiry closest to this many days out
STRIKES_PER_EXPIRY = 2


def select_expiries(expiries: List[date], today: date) -> List[date]:
    """The nearest expiry >= 2 days out, and the one closest to 30 days out (if different)."""
    candidates = [e for e in expiries if e >= today + timedelta(days=WEEKLY_MIN_DAYS)]
    if not candidates:
        return []
    weekly = candidates[0]
    target = today + timedelta(days=MONTHLY_TARGET_DAYS)
    others = [e for e in candidates if e != weekly]
    monthly = min(others, key=lambda e: (abs((e - target).days), e)) if others else None
    return [weekly] + ([monthly] if monthly else [])


def select_contracts(chain: Chain, n: int = STRIKES_PER_EXPIRY) -> List[OptionQuote]:
    """Calls and puts at the `n` strikes nearest the underlying price (listed for both)."""
    calls = {q.strike: q for q in chain.quotes if q.right == "C"}
    puts = {q.strike: q for q in chain.quotes if q.right == "P"}
    strikes = sorted(set(calls) & set(puts), key=lambda s: (abs(s - chain.underlying_price), s))[:n]
    return [q for s in sorted(strikes) for q in (calls[s], puts[s])]
