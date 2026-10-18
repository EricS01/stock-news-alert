"""Postgres access: connection, migrations, and the queries the jobs need.

Plain SQL on purpose. Run migrations with:  python -m alerts.db migrate
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Set

import psycopg
from psycopg.types.json import Jsonb

from .config import ROOT
from .models import NewsItem

log = logging.getLogger(__name__)

MIGRATIONS_DIR = ROOT / "migrations"
MIGRATION_LOCK_ID = 7_150_001  # arbitrary constant for pg_advisory_lock


def connect(url: Optional[str] = None) -> psycopg.Connection:
    """Autocommit connection; use `with conn.transaction():` to group writes."""
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    # No server-side prepared statements: Neon's pooled endpoint (PgBouncer) may hand
    # each transaction a different backend.
    return psycopg.connect(url, autocommit=True, prepare_threshold=None)


# --- migrations -------------------------------------------------------------


def migration_files(directory: Path = MIGRATIONS_DIR) -> List[Path]:
    return sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))


def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> List[str]:
    """Apply pending migrations in order, each in its own transaction. Returns names applied."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    # Session-level lock so two runs starting at once don't both apply a migration.
    conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
    try:
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
        applied = []
        for path in migration_files(directory):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            log.info("applied migration %s", path.name)
            applied.append(path.name)
        return applied
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_ID,))


# --- runs -------------------------------------------------------------------


def start_run(conn: psycopg.Connection, job: str) -> int:
    return conn.execute("INSERT INTO runs (job) VALUES (%s) RETURNING id", (job,)).fetchone()[0]


def finish_run(conn: psycopg.Connection, run_id: int, status: str, counts: dict, error: Optional[str] = None) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = now(), status = %s, counts = %s, error = %s WHERE id = %s",
        (status, Jsonb(counts), error, run_id),
    )


def last_ok_run_start(conn: psycopg.Connection, job: str) -> Optional[datetime]:
    row = conn.execute(
        "SELECT max(started_at) FROM runs WHERE job = %s AND status = 'ok'", (job,)
    ).fetchone()
    return row[0]


# --- news items and scores --------------------------------------------------


def existing_item_ids(conn: psycopg.Connection, ids: Iterable[str]) -> Set[str]:
    ids = list(ids)
    if not ids:
        return set()
    rows = conn.execute("SELECT id FROM news_items WHERE id = ANY(%s)", (ids,))
    return {r[0] for r in rows}


def ensure_prompt_version(
    conn: psycopg.Connection, version: str, model: str, system_prompt: str, notes: str = ""
) -> None:
    """Record a rubric. New versions start active only if none is."""
    conn.execute(
        """
        INSERT INTO prompt_versions (id, model, system_prompt, status, notes)
        VALUES (%s, %s, %s,
                CASE WHEN EXISTS (SELECT 1 FROM prompt_versions WHERE status = 'active')
                     THEN 'shadow' ELSE 'active' END,
                nullif(%s, ''))
        ON CONFLICT (id) DO NOTHING
        """,
        (version, model, system_prompt, notes),
    )


def prompt_versions(conn: psycopg.Connection) -> List[tuple]:
    """Rows: (id, model, system_prompt, notes, status), oldest first."""
    return conn.execute(
        "SELECT id, model, system_prompt, coalesce(notes, ''), status FROM prompt_versions ORDER BY created_at, id"
    ).fetchall()


def insert_items(conn: psycopg.Connection, items: List[NewsItem]) -> None:
    # executemany pipelines the statements: one round trip per batch, not per row.
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO news_items (id, source, title, url, text, published_at, meta)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            [(i.id, i.source, i.title, i.url, i.text, i.published_at, Jsonb(i.meta)) for i in items],
        )


def insert_scores(conn: psycopg.Connection, rows: List[dict]) -> None:
    """Each row is a dict keyed by the column names below."""
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO scores (item_id, ticker, prompt_version, score, direction, reason, floor_applied,
                                alerted, suppressed)
            VALUES (%(item_id)s, %(ticker)s, %(prompt_version)s, %(score)s, %(direction)s, %(reason)s,
                    %(floor_applied)s, %(alerted)s, %(suppressed)s)
            ON CONFLICT (item_id, ticker, prompt_version) DO NOTHING
            """,
            [{"suppressed": None, **r} for r in rows],
        )


def recent_alerts(conn: psycopg.Connection, since: datetime) -> List[tuple]:
    """Alerts pushed since `since`. Rows: (ticker, title, score, direction)."""
    return conn.execute(
        """
        SELECT s.ticker, n.title, s.score, s.direction
        FROM scores s JOIN news_items n ON n.id = s.item_id
        WHERE s.alerted AND s.scored_at >= %s
        """,
        (since,),
    ).fetchall()


# --- price outcomes ---------------------------------------------------------


def pending_outcomes(conn: psycopg.Connection) -> List[tuple]:
    """Scored (item, ticker) pairs with no outcome yet, or one still pending.

    Rows: (item_id, ticker, first_scored_at, entry_ts, entry_price, spy_entry_price).
    """
    return conn.execute(
        """
        SELECT s.item_id, s.ticker, min(s.scored_at), p.entry_ts, p.entry_price, p.spy_entry_price
        FROM scores s
        LEFT JOIN price_outcomes p USING (item_id, ticker)
        WHERE p.status IS NULL OR p.status = 'pending'
        GROUP BY s.item_id, s.ticker, p.entry_ts, p.entry_price, p.spy_entry_price
        ORDER BY 3
        """
    ).fetchall()


OUTCOME_COLUMNS = (
    "item_id", "ticker", "first_scored_at", "entry_ts", "entry_price", "spy_entry_price",
    "ret_1d", "ret_3d", "ret_5d", "spy_ret_1d", "spy_ret_3d", "spy_ret_5d", "status",
)


def upsert_outcomes(conn: psycopg.Connection, rows: List[dict]) -> None:
    cols = ", ".join(OUTCOME_COLUMNS)
    values = ", ".join(f"%({c})s" for c in OUTCOME_COLUMNS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in OUTCOME_COLUMNS[2:])
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            f"""
            INSERT INTO price_outcomes ({cols}) VALUES ({values})
            ON CONFLICT (item_id, ticker) DO UPDATE SET {updates}, labeled_at = now()
            """,
            rows,
        )


# --- option snapshots -------------------------------------------------------


def enqueue_snapshots(conn: psycopg.Connection, pairs: List[tuple]) -> None:
    """Queue (item_id, ticker) pairs for an entry options snapshot."""
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO option_snapshot_queue (item_id, ticker) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            pairs,
        )


def open_snapshot_queue(conn: psycopg.Connection) -> List[tuple]:
    """Rows: (item_id, ticker, queued_at), oldest first."""
    return conn.execute(
        "SELECT item_id, ticker, queued_at FROM option_snapshot_queue WHERE done_at IS NULL ORDER BY queued_at"
    ).fetchall()


def finish_snapshot_queue(conn: psycopg.Connection, done: List[tuple], failed: List[tuple]) -> None:
    """`done`: (item_id, ticker). `failed`: (item_id, ticker, error, give_up)."""
    with conn.cursor() as cur:
        cur.executemany(
            "UPDATE option_snapshot_queue SET done_at = now(), attempts = attempts + 1, last_error = NULL"
            " WHERE item_id = %s AND ticker = %s",
            done,
        )
        cur.executemany(
            """
            UPDATE option_snapshot_queue
            SET attempts = attempts + 1, last_error = %s, done_at = CASE WHEN %s THEN now() END
            WHERE item_id = %s AND ticker = %s
            """,
            [(error, give_up, item_id, ticker) for item_id, ticker, error, give_up in failed],
        )


SNAPSHOT_COLUMNS = (
    "item_id", "ticker", "contract_symbol", "expiry", "strike", "right", "kind", "taken_at",
    "underlying_price", "bid", "ask", "last", "iv", "volume", "open_interest",
)


def insert_option_snapshots(conn: psycopg.Connection, rows: List[dict]) -> None:
    cols = ", ".join(f'"{c}"' for c in SNAPSHOT_COLUMNS)
    values = ", ".join(f"%({c})s" for c in SNAPSHOT_COLUMNS)
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO option_snapshots ({cols}) VALUES ({values})"
            " ON CONFLICT (item_id, ticker, contract_symbol, kind) DO NOTHING",
            rows,
        )


def recent_option_snapshots(conn: psycopg.Connection, since: datetime) -> List[tuple]:
    """Snapshots for entries taken since `since`.
    Rows: (item_id, ticker, kind, contract_symbol, expiry, taken_at)."""
    return conn.execute(
        """
        SELECT o.item_id, o.ticker, o.kind, o.contract_symbol, o.expiry, o.taken_at
        FROM option_snapshots o
        WHERE (o.item_id, o.ticker) IN (
            SELECT item_id, ticker FROM option_snapshots WHERE kind = 'entry' AND taken_at >= %s
        )
        """,
        (since,),
    ).fetchall()


def main() -> None:
    parser = argparse.ArgumentParser(description="Database utilities")
    parser.add_argument("command", choices=["migrate"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.command == "migrate":
        with connect() as conn:
            applied = migrate(conn)
        log.info("migrations up to date (%d applied)", len(applied))


if __name__ == "__main__":
    main()
