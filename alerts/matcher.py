from __future__ import annotations

import re
from typing import Dict, List, Pattern, Tuple

from .models import NewsItem


class Matcher:
    """Finds which watchlist tickers a news item is about.

    Tickers are matched case-sensitively (as "$NVDA" or a standalone "NVDA")
    so words like "meta" don't trigger. Company aliases are case-insensitive.
    """

    def __init__(self, stocks: Dict[str, List[str]]):
        self.patterns: Dict[str, Tuple[Pattern, Pattern]] = {}
        for ticker, aliases in stocks.items():
            ticker_re = re.compile(r"(?:\$|\b)%s\b(?!-)" % re.escape(ticker))
            alias_re = (
                re.compile(r"\b(?:%s)\b" % "|".join(re.escape(a) for a in aliases), re.IGNORECASE)
                if aliases
                else None
            )
            self.patterns[ticker] = (ticker_re, alias_re)

    def match_text(self, text: str) -> List[str]:
        hits = []
        for ticker, (ticker_re, alias_re) in self.patterns.items():
            if ticker_re.search(text) or (alias_re and alias_re.search(text)):
                hits.append(ticker)
        return hits

    def match(self, item: NewsItem) -> List[str]:
        tickers = self.match_text(f"{item.title}\n{item.text}")
        item.tickers = tickers
        return tickers
