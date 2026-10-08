from __future__ import annotations

import re
from typing import Dict, List, Pattern

from .models import NewsItem


class Matcher:
    """Finds which watchlist tickers a news item is about."""

    def __init__(self, stocks: Dict[str, List[str]]):
        self.patterns: Dict[str, Pattern] = {}
        for ticker, aliases in stocks.items():
            terms = [re.escape(ticker)] + [re.escape(a) for a in aliases]
            self.patterns[ticker] = re.compile(r"\b(?:%s)\b" % "|".join(terms), re.IGNORECASE)

    def match_text(self, text: str) -> List[str]:
        return [t for t, p in self.patterns.items() if p.search(text)]

    def match(self, item: NewsItem) -> List[str]:
        tickers = self.match_text(f"{item.title}\n{item.text}")
        item.tickers = tickers
        return tickers
