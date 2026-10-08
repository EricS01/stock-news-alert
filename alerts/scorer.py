from __future__ import annotations

import json
import logging
from typing import List, Optional, Tuple

import anthropic

from .models import Alert, NewsItem

log = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
BATCH_SIZE = 10

SYSTEM = """You are a sell-side equity analyst triaging a news feed for a retail investor.
For each numbered (headline, ticker) pair, judge how likely the news is to move that
stock's price in the next few trading days.

Score 1-5:
1 = irrelevant, or the company is only mentioned in passing (e.g. "built with Google Fonts")
2 = relevant but routine, no price impact expected
3 = noteworthy, minor impact possible
4 = material: earnings surprise, guidance change, major product/regulatory/legal event, exec departure, big contract
5 = major: M&A, fraud/investigation, outage or recall at scale, bankruptcy-level risk

direction: bullish, bearish, or neutral for the given ticker.
reason: one short sentence a phone notification can show."""

SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "score": {"type": "integer"},
                    "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
                    "reason": {"type": "string"},
                },
                "required": ["index", "score", "direction", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

Pair = Tuple[NewsItem, str]


def build_prompt(pairs: List[Pair]) -> str:
    lines = []
    for i, (item, ticker) in enumerate(pairs):
        snippet = " ".join(item.text.split())[:300]
        lines.append(f"[{i}] ticker={ticker} source={item.source}\n    headline: {item.title}")
        if snippet:
            lines.append(f"    excerpt: {snippet}")
    return "Score each pair:\n\n" + "\n".join(lines)


def parse_results(text: str, pairs: List[Pair]) -> List[Alert]:
    alerts = []
    for r in json.loads(text).get("results", []):
        idx = r.get("index")
        if not isinstance(idx, int) or not 0 <= idx < len(pairs):
            continue
        item, ticker = pairs[idx]
        alerts.append(
            Alert(
                item=item,
                ticker=ticker,
                score=max(1, min(5, int(r["score"]))),
                direction=r.get("direction", "neutral"),
                reason=r.get("reason", "").strip(),
            )
        )
    return alerts


class Scorer:
    def __init__(self, client: Optional[anthropic.Anthropic] = None):
        self.client = client or anthropic.Anthropic()

    def _score_batch(self, pairs: List[Pair]) -> List[Alert]:
        response = self.client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM,
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
            messages=[{"role": "user", "content": build_prompt(pairs)}],
        )
        if response.stop_reason == "refusal":
            log.warning("scorer refused a batch of %d items; skipping", len(pairs))
            return []
        text = next(b.text for b in response.content if b.type == "text")
        return parse_results(text, pairs)

    def score(self, pairs: List[Pair]) -> List[Alert]:
        alerts: List[Alert] = []
        for start in range(0, len(pairs), BATCH_SIZE):
            batch = pairs[start : start + BATCH_SIZE]
            try:
                alerts += self._score_batch(batch)
            except anthropic.APIError as e:
                log.warning("scoring batch failed: %s", e)
        return alerts
