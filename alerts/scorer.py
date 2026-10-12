from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import anthropic

from .models import Alert, NewsItem

log = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
PROMPT_VERSION = "v1"  # bump when SYSTEM or MODEL changes, so scores stay comparable
BATCH_SIZE = 10
WORKERS = 4  # batches scored concurrently

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


@dataclass
class ScoreResult:
    alerts: List[Alert] = field(default_factory=list)
    failed: List[Pair] = field(default_factory=list)  # pairs whose batch errored; retry later
    batches: int = 0
    failed_batches: int = 0

    @property
    def all_failed(self) -> bool:
        return self.batches > 0 and self.failed_batches == self.batches


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


def make_batches(pairs: List[Pair], size: int = BATCH_SIZE) -> List[List[Pair]]:
    """Split pairs into batches of about `size`, never splitting one item's pairs.

    Keeping an item in one batch means its scoring succeeds or fails as a whole,
    so a failed item can simply be retried later.
    """
    batches: List[List[Pair]] = []
    current: List[Pair] = []
    for item_id in dict.fromkeys(item.id for item, _ in pairs):  # first-seen order
        group = [p for p in pairs if p[0].id == item_id]
        if current and len(current) + len(group) > size:
            batches.append(current)
            current = []
        current += group
    if current:
        batches.append(current)
    return batches


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

    def _try_batch(self, batch: List[Pair]) -> Optional[List[Alert]]:
        try:
            return self._score_batch(batch)
        except anthropic.APIError as e:
            log.warning("scoring batch failed: %s", e)
            return None

    def score(self, pairs: List[Pair]) -> ScoreResult:
        batches = make_batches(pairs)
        result = ScoreResult(batches=len(batches))
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for batch, alerts in zip(batches, pool.map(self._try_batch, batches)):
                if alerts is None:
                    result.failed_batches += 1
                    result.failed += batch
                else:
                    result.alerts += alerts
        return result
