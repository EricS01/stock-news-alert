import pytest

from alerts import db
from alerts.main import RunFailed, dedupe, process, record_run
from alerts.matcher import Matcher
from alerts.models import Alert, NewsItem
from alerts.scorer import ScoreResult

STOCKS = {"NVDA": ["Nvidia"], "AAPL": ["Apple"]}


class FakeNotifier:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send(self, alert):
        if self.fail:
            raise RuntimeError("ntfy down")
        self.sent.append((alert.item.id, alert.ticker))


def scorer_returning(scores, failed_ids=()):
    """Fake scorer: `scores` maps (item_id, ticker) -> score; pairs of failed_ids error."""
    def score(pairs):
        result = ScoreResult(batches=1 if pairs else 0)
        for item, ticker in pairs:
            if item.id in failed_ids:
                result.failed.append((item, ticker))
            else:
                result.alerts.append(Alert(item=item, ticker=ticker, score=scores.get((item.id, ticker), 1),
                                           direction="bearish", reason="r"))
        if pairs and len(result.failed) == len(pairs):
            result.failed_batches = 1
        return result
    return score


def _items():
    return [
        NewsItem(id="hn:1", source="hackernews", title="Nvidia under DOJ probe", url="u1"),
        NewsItem(id="hn:2", source="hackernews", title="Apple ships a thing", url="u2"),
        NewsItem(id="hn:3", source="hackernews", title="Rust 2.0 released", url="u3"),
    ]


def _process(conn, items, scorer, notifier=None, dry_run=False):
    return process(conn, items, Matcher(STOCKS), notifier or FakeNotifier(), scorer, threshold=4, dry_run=dry_run)


def test_dedupe_merges_tickers():
    a = NewsItem(id="n:1", source="news", title="t", url="u", tickers=["NVDA"])
    b = NewsItem(id="n:1", source="news", title="t", url="u", tickers=["AAPL"])
    out = dedupe([a, b])
    assert len(out) == 1 and out[0].tickers == ["NVDA", "AAPL"]


def test_pushes_only_at_threshold_without_db():
    notifier = FakeNotifier()
    counts = _process(None, _items(), scorer_returning({("hn:1", "NVDA"): 4}), notifier)
    assert notifier.sent == [("hn:1", "NVDA")]
    assert counts["pairs"] == 2 and counts["alerts"] == 1


def test_all_batches_failing_fails_the_run():
    with pytest.raises(RunFailed, match="scoring batches failed"):
        _process(None, _items(), scorer_returning({}, failed_ids={"hn:1", "hn:2"}))


def test_push_failure_fails_the_run_after_processing():
    with pytest.raises(RunFailed, match="push notification"):
        _process(None, _items(), scorer_returning({("hn:1", "NVDA"): 5}), FakeNotifier(fail=True))


def test_records_items_and_all_scores(conn):
    db.ensure_prompt_version(conn, "v1", "m", "p")
    _process(conn, _items(), scorer_returning({("hn:1", "NVDA"): 4}))
    assert db.existing_item_ids(conn, ["hn:1", "hn:2", "hn:3"]) == {"hn:1", "hn:2", "hn:3"}
    rows = conn.execute("SELECT item_id, ticker, score, alerted FROM scores ORDER BY item_id").fetchall()
    assert rows == [("hn:1", "NVDA", 4, True), ("hn:2", "AAPL", 1, False)]


def test_items_already_in_db_are_skipped(conn):
    db.ensure_prompt_version(conn, "v1", "m", "p")
    _process(conn, _items(), scorer_returning({}))
    notifier = FakeNotifier()
    counts = _process(conn, _items(), scorer_returning({("hn:1", "NVDA"): 5}), notifier)
    assert counts["new_items"] == 0 and notifier.sent == []


def test_items_with_failed_scoring_are_retried(conn):
    db.ensure_prompt_version(conn, "v1", "m", "p")
    _process(conn, _items(), scorer_returning({}, failed_ids={"hn:1"}))
    assert db.existing_item_ids(conn, ["hn:1", "hn:2"]) == {"hn:2"}
    counts = _process(conn, _items(), scorer_returning({}))
    assert counts["new_items"] == 1
    assert db.existing_item_ids(conn, ["hn:1"]) == {"hn:1"}


def test_sec_floor_is_recorded(conn):
    db.ensure_prompt_version(conn, "v1", "m", "p")
    filing = NewsItem(id="sec:1", source="sec", title="NVDA filed 8-K", url="u", tickers=["NVDA"],
                      meta={"form": "8-K", "items": "2.02"})
    _process(conn, [filing], scorer_returning({}))
    assert conn.execute("SELECT score, floor_applied, alerted FROM scores").fetchone() == (4, True, True)


def test_dry_run_writes_nothing(conn):
    _process(conn, _items(), scorer_returning({}), dry_run=True)
    assert db.existing_item_ids(conn, ["hn:1", "hn:2", "hn:3"]) == set()


def test_record_run_marks_failure(conn):
    def work():
        raise RunFailed("all 2 scoring batches failed", {"pairs": 12})
    with pytest.raises(RunFailed):
        record_run(conn, work)
    status, counts, error = conn.execute("SELECT status, counts, error FROM runs").fetchone()
    assert status == "failed" and error == "all 2 scoring batches failed"
    assert counts["pairs"] == 12 and "duration_s" in counts


def test_record_run_ok(conn):
    record_run(conn, lambda: {"pairs": 0})
    assert conn.execute("SELECT status FROM runs").fetchone() == ("ok",)
    assert db.last_ok_run_start(conn, "alerts") is not None
