from datetime import datetime, timezone

import psycopg
import pytest

from alerts import db
from alerts.models import NewsItem


def _item(id="hn:1", **kw):
    return NewsItem(
        id=id, source="hackernews", title="Nvidia under DOJ probe", url="https://x",
        published_at=datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc), meta={"points": 10}, **kw,
    )


def test_migration_files_are_ordered_and_named():
    names = [p.name for p in db.migration_files()]
    assert names and names == sorted(names)
    assert names[0] == "001_init.sql"


def test_migrate_is_idempotent(conn):
    assert db.migrate(conn) == []
    applied = [r[0] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY name")]
    assert applied == [p.name for p in db.migration_files()]


def test_item_roundtrip_and_existing_ids(conn):
    db.insert_items(conn, [_item()])
    db.insert_items(conn, [_item()])  # duplicate insert is a no-op
    db.insert_items(conn, [])
    assert db.existing_item_ids(conn, ["hn:1", "hn:2"]) == {"hn:1"}
    assert db.existing_item_ids(conn, []) == set()
    meta, published = conn.execute("SELECT meta, published_at FROM news_items").fetchone()
    assert meta == {"points": 10}
    assert published == datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)


def test_first_prompt_version_is_active_later_ones_shadow(conn):
    db.ensure_prompt_version(conn, "v1", "m", "prompt one")
    db.ensure_prompt_version(conn, "v1", "m", "changed text is ignored")
    db.ensure_prompt_version(conn, "v2", "m", "prompt two")
    rows = conn.execute("SELECT id, status, system_prompt FROM prompt_versions ORDER BY id").fetchall()
    assert rows == [("v1", "active", "prompt one"), ("v2", "shadow", "prompt two")]


def test_scores_are_unique_per_item_ticker_version(conn):
    db.ensure_prompt_version(conn, "v1", "m", "p")
    db.insert_items(conn, [_item()])
    kw = dict(item_id="hn:1", ticker="NVDA", prompt_version="v1", direction="bearish",
              reason="probe", floor_applied=False, alerted=True)
    db.insert_scores(conn, [dict(score=4, **kw)])
    db.insert_scores(conn, [dict(score=2, **kw)])
    assert conn.execute("SELECT score, alerted FROM scores").fetchall() == [(4, True)]


def test_score_must_reference_an_item(conn):
    db.ensure_prompt_version(conn, "v1", "m", "p")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.insert_scores(conn, [dict(item_id="nope", ticker="NVDA", prompt_version="v1", score=3,
                                     direction="neutral", reason="", floor_applied=False, alerted=False)])


def test_runs_track_last_ok_start(conn):
    assert db.last_ok_run_start(conn, "alerts") is None
    ok = db.start_run(conn, "alerts")
    db.finish_run(conn, ok, "ok", {"pairs": 3})
    failed = db.start_run(conn, "alerts")
    db.finish_run(conn, failed, "failed", {}, error="boom")
    started = conn.execute("SELECT started_at FROM runs WHERE id = %s", (ok,)).fetchone()[0]
    assert db.last_ok_run_start(conn, "alerts") == started
    assert db.last_ok_run_start(conn, "label") is None
    row = conn.execute("SELECT status, counts, error, finished_at IS NOT NULL FROM runs WHERE id = %s", (failed,)).fetchone()
    assert row == ("failed", {}, "boom", True)
