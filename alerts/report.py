"""The `report` job: run every query in sql/report/, publish the results as the
Actions job summary, and push a one-line headline to ntfy.

Each NN_name.sql file is one section. Its first comment lines give the section
`-- title:` and an optional `-- note:`. Queries may use %(since)s (start of the
week) and must not use a literal percent sign.

Run with:  python -m alerts.report [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Optional, Sequence

from . import db
from .config import ROOT
from .notifier import Notifier
from .runs import RunFailed, record_run

log = logging.getLogger("alerts.report")

JOB = "report"
REPORT_DIR = ROOT / "sql" / "report"
PERIOD = timedelta(days=7)


@dataclass
class Section:
    title: str
    note: str
    columns: List[str]
    rows: List[tuple]


def parse_header(sql: str, default_title: str):
    title, note = default_title, ""
    for line in sql.splitlines():
        if not line.startswith("--"):
            break
        key, _, value = line[2:].strip().partition(":")
        if key == "title":
            title = value.strip()
        elif key == "note":
            note = value.strip()
    return title, note


def query(conn, sql: str, since: datetime):
    params = {"since": since} if "%(since)s" in sql else None
    cur = conn.execute(sql, params)
    return [c.name for c in cur.description], cur.fetchall()


def run_sections(conn, since: datetime, directory: Path = REPORT_DIR) -> List[Section]:
    sections = []
    for path in sorted(directory.glob("[0-9][0-9]_*.sql")):
        sql = path.read_text()
        title, note = parse_header(sql, path.stem)
        columns, rows = query(conn, sql, since)
        sections.append(Section(title, note, columns, rows))
    return sections


def fmt(value) -> str:
    if value is None:
        return "–"
    if isinstance(value, (float, Decimal)):
        return f"{value:,.2f}".rstrip("0").rstrip(".") if value % 1 else f"{value:,.0f}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def markdown_table(columns: Sequence[str], rows: Sequence[tuple]) -> str:
    if not rows:
        return "_No data yet._"
    lines = [
        "| " + " | ".join(c.replace("_", " ") for c in columns) + " |",
        "|" + "---|" * len(columns),
    ]
    lines += ["| " + " | ".join(fmt(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def to_markdown(sections: List[Section], since: datetime, now: datetime) -> str:
    parts = [f"# Weekly research report\n\n{since:%b %d} – {now:%b %d %Y}"]
    for s in sections:
        parts.append(f"## {s.title}\n\n" + (f"_{s.note}_\n\n" if s.note else "") + markdown_table(s.columns, s.rows))
    return "\n\n".join(parts) + "\n"


def headline(conn, since: datetime):
    """(title, message) for the ntfy push."""
    columns, rows = query(conn, (REPORT_DIR / "headline.sql").read_text(), since)
    h = dict(zip(columns, rows[0]))
    title = f"Weekly report: {h['scores']} scores, {h['pushed']} pushed"
    if not h["labeled_days"]:
        return title, "No price outcomes yet."
    lines = [
        f"1-day move vs SPY: {fmt(h['high_move_1d'])}% on days scored 4-5, {fmt(h['low_move_1d'])}% on days "
        f"scored 1-2 ({h['labeled_days']} ticker-days).",
    ]
    if h["calls"]:
        lines.append(
            f"Direction right on {fmt(h['hit_rate_1d'])}% of {h['calls']} days scored 4+ "
            f"(95% range {fmt(h['hit_low'])}-{fmt(h['hit_high'])}%)."
        )
    return title, "\n".join(lines)


def actions_run_url() -> Optional[str]:
    parts = [os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")]
    return "{}/{}/actions/runs/{}".format(*parts) if all(parts) else None


def build_and_publish(conn, notifier: Notifier, now: Optional[datetime] = None, dry_run: bool = False) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - PERIOD
    sections = run_sections(conn, since)
    report = to_markdown(sections, since, now)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and not dry_run:
        with open(summary, "a") as f:
            f.write(report)
    else:
        print(report)
    title, message = headline(conn, since)
    notifier.send_message(title, message, click=actions_run_url())
    return {"sections": len(sections), "rows": sum(len(s.rows) for s in sections)}


def run(dry_run: bool = False) -> None:
    if not os.environ.get("DATABASE_URL"):
        raise RunFailed("DATABASE_URL is not set")
    notifier = Notifier(dry_run=dry_run)
    with db.connect() as conn:
        if dry_run:
            build_and_publish(conn, notifier, dry_run=True)
        else:
            record_run(conn, JOB, lambda: build_and_publish(conn, notifier))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weekly research report")
    parser.add_argument("--dry-run", action="store_true", help="print the report and the push instead of publishing")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(dry_run=args.dry_run)
