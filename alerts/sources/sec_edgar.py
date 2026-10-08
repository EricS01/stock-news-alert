from __future__ import annotations

import calendar
import logging
import os
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests

from ..models import NewsItem

log = logging.getLogger(__name__)

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
DEFAULT_LOOKBACK = 24 * 3600

FORMS = {
    "8-K": "current report (material event)",
    "10-Q": "quarterly report",
    "10-K": "annual report",
    "4": "insider transaction",
}

# CIKs for the default watchlist, so we don't depend on the ticker map endpoint.
KNOWN_CIKS = {
    "AAPL": 320193, "MSFT": 789019, "NVDA": 1045810, "GOOGL": 1652044,
    "AMZN": 1018724, "META": 1326801, "TSLA": 1318605,
}


def user_agent() -> str:
    # SEC requires a descriptive User-Agent with contact info:
    # https://www.sec.gov/os/accessing-edgar-data
    return os.environ.get("SEC_USER_AGENT", "stock-news-alerts personal-project")


def resolve_ciks(tickers: List[str], session: requests.Session) -> Dict[str, int]:
    ciks = {t: KNOWN_CIKS[t] for t in tickers if t in KNOWN_CIKS}
    missing = [t for t in tickers if t not in ciks]
    if missing:
        try:
            data = session.get(TICKER_MAP, timeout=20).json()
            by_ticker = {row["ticker"].upper(): row["cik_str"] for row in data.values()}
            ciks.update({t: by_ticker[t] for t in missing if t in by_ticker})
        except Exception as e:
            log.warning("could not resolve CIKs for %s: %s", missing, e)
    return ciks


def parse_submissions(data: dict, ticker: str, cik: int, since: float) -> List[NewsItem]:
    recent = data.get("filings", {}).get("recent", {})
    items = []
    for i, form in enumerate(recent.get("form", [])):
        if form not in FORMS:
            continue
        accepted = datetime.strptime(recent["acceptanceDateTime"][i][:19], "%Y-%m-%dT%H:%M:%S")
        ts = calendar.timegm(accepted.timetuple())  # acceptanceDateTime is UTC
        if ts <= since:
            continue
        acc = recent["accessionNumber"][i]
        sec_items = recent.get("items", [""] * (i + 1))[i]
        title = f"{ticker} filed {form} ({FORMS[form]})"
        if sec_items:
            title += f" — items {sec_items}"
        items.append(
            NewsItem(
                id=f"sec:{acc}",
                source="sec",
                title=title,
                url=ARCHIVE.format(cik=cik, acc=acc.replace("-", ""), doc=recent["primaryDocument"][i]),
                text=recent.get("primaryDocDescription", [""] * (i + 1))[i],
                tickers=[ticker],
                meta={"form": form, "items": sec_items},
            )
        )
    return items


def fetch(tickers: List[str], since: Optional[float] = None) -> List[NewsItem]:
    since = since or time.time() - DEFAULT_LOOKBACK
    session = requests.Session()
    session.headers["User-Agent"] = user_agent()
    items: List[NewsItem] = []
    for ticker, cik in resolve_ciks(tickers, session).items():
        resp = session.get(SUBMISSIONS.format(cik=cik), timeout=20)
        resp.raise_for_status()
        items += parse_submissions(resp.json(), ticker, cik, since)
        time.sleep(0.15)  # SEC fair-access limit is 10 req/s
    return items


# 8-K items that are almost always price-moving: earnings, M&A, exec changes.
HIGH_IMPACT_8K_ITEMS = {"1.01", "2.01", "2.02", "5.02"}


def score_floor(item: NewsItem) -> int:
    """Minimum impact score for a filing, regardless of what the model says."""
    if item.source != "sec" or item.meta.get("form") != "8-K":
        return 0
    filed = {x.strip() for x in (item.meta.get("items") or "").split(",")}
    return 4 if filed & HIGH_IMPACT_8K_ITEMS else 0
