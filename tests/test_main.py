import pytest

from alerts import db
from alerts.main import dedupe, process
from alerts.runs import RunFailed, record_run
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
        record_run(conn, "alerts", work)
    status, counts, error = conn.execute("SELECT status, counts, error FROM runs").fetchone()
    assert status == "failed" and error == "all 2 scoring batches failed"
    assert counts["pairs"] == 12 and "duration_s" in counts


def test_record_run_ok(conn):
    record_run(conn, "alerts", lambda: {"pairs": 0})
    assert conn.execute("SELECT status FROM runs").fetchone() == ("ok",)
    assert db.last_ok_run_start(conn, "alerts") is not None


def test_alerts_at_threshold_are_queued_for_options(conn):
    db.ensure_prompt_version(conn, "v1", "m", "p")
    _process(conn, _items(), scorer_returning({("hn:1", "NVDA"): 4, ("hn:2", "AAPL"): 3}))
    assert [r[:2] for r in db.open_snapshot_queue(conn)] == [("hn:1", "NVDA")]


def _story(id, title):
    return NewsItem(id=id, source="news", title=title, url=id)


def test_syndicated_story_is_pushed_once(conn):
    db.ensure_prompt_version(conn, "v1", "m", "p")
    items = [_story("n:1", "Nvidia under DOJ probe - Reuters"), _story("n:2", "Nvidia under DOJ probe - CNBC")]
    notifier = FakeNotifier()
    counts = _process(conn, items, scorer_returning({("n:1", "NVDA"): 4, ("n:2", "NVDA"): 5}), notifier)
    assert notifier.sent == [("n:2", "NVDA")]  # the higher score wins
    assert counts["alerts"] == 1 and counts["suppressed_duplicate"] == 1
    rows = conn.execute("SELECT item_id, alerted, suppressed FROM scores ORDER BY item_id").fetchall()
    assert rows == [("n:1", False, "duplicate"), ("n:2", True, None)]
    assert [r[:2] for r in db.open_snapshot_queue(conn)] == [("n:2", "NVDA")]  # only the pushed one


def test_cooldown_applies_across_runs(conn):
    db.ensure_prompt_version(conn, "v1", "m", "p")
    _process(conn, [_story("n:1", "Nvidia beats estimates")], scorer_returning({("n:1", "NVDA"): 4}))
    notifier = FakeNotifier()
    counts = _process(conn, [_story("n:2", "Nvidia raises guidance"), _story("n:3", "Nvidia announces buyback")],
                      scorer_returning({("n:2", "NVDA"): 4, ("n:3", "NVDA"): 5}), notifier)
    assert notifier.sent == [("n:3", "NVDA")]  # a higher score still gets through
    assert counts["suppressed_cooldown"] == 1


def test_cooldown_expires(conn):
    db.ensure_prompt_version(conn, "v1", "m", "p")
    _process(conn, [_story("n:1", "Nvidia beats estimates")], scorer_returning({("n:1", "NVDA"): 4}))
    conn.execute("UPDATE scores SET scored_at = now() - interval '13 hours'")
    notifier = FakeNotifier()
    _process(conn, [_story("n:2", "Nvidia raises guidance")], scorer_returning({("n:2", "NVDA"): 4}), notifier)
    assert notifier.sent == [("n:2", "NVDA")]


def test_shadow_rubric_scores_the_same_pairs_but_never_pushes(conn):
    db.ensure_prompt_version(conn, "v1", "m", "p")
    db.ensure_prompt_version(conn, "v2", "m", "p2")
    notifier = FakeNotifier()
    counts = process(conn, _items(), Matcher(STOCKS), notifier, scorer_returning({("hn:1", "NVDA"): 2}), threshold=4,
                     version="v1", shadows={"v2": scorer_returning({("hn:1", "NVDA"): 5, ("hn:2", "AAPL"): 4})})
    assert notifier.sent == [] and counts["shadows"] == {"v2": {"scored": 2, "failed_pairs": 0}}
    rows = conn.execute("SELECT prompt_version, item_id, score, alerted FROM scores ORDER BY 1, 2").fetchall()
    assert rows == [("v1", "hn:1", 2, False), ("v1", "hn:2", 1, False),
                    ("v2", "hn:1", 5, False), ("v2", "hn:2", 4, False)]
    assert db.open_snapshot_queue(conn) == []


def test_failing_shadow_never_fails_the_run(conn):
    db.ensure_prompt_version(conn, "v1", "m", "p")
    db.ensure_prompt_version(conn, "v2", "m", "p2")

    def broken(pairs):
        raise RuntimeError("bad prompt")

    notifier = FakeNotifier()
    counts = process(conn, _items(), Matcher(STOCKS), notifier, scorer_returning({("hn:1", "NVDA"): 4}), threshold=4,
                     version="v1", shadows={"v2": broken})
    assert notifier.sent == [("hn:1", "NVDA")]
    assert counts["shadows"]["v2"] == {"scored": 0, "failed_pairs": 2}
    assert conn.execute("SELECT count(*) FROM scores WHERE prompt_version = 'v2'").fetchone()[0] == 0


def test_shadow_scores_are_only_kept_for_processed_items(conn):
    db.ensure_prompt_version(conn, "v1", "m", "p")
    db.ensure_prompt_version(conn, "v2", "m", "p2")
    process(conn, _items(), Matcher(STOCKS), FakeNotifier(), scorer_returning({}, failed_ids={"hn:1"}), threshold=4,
            version="v1", shadows={"v2": scorer_returning({})})
    # hn:1 failed for the active rubric, so it isn't processed yet; its shadow score would have no news_items row.
    assert [r[0] for r in conn.execute("SELECT item_id FROM scores WHERE prompt_version = 'v2'")] == ["hn:2"]
