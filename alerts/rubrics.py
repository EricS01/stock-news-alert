"""Scoring rubrics (prompt versions).

Each rubric is a file prompts/vN.md: a small header (`model:`, `notes:`), a `---`
line, then the system prompt. Every alerts run syncs new files into the
prompt_versions table. The first version becomes `active`, later ones `shadow`.
Rows are never edited afterwards: the database records exactly what ran, so to
change a rubric, add a new file.

Every run scores the same pairs with the active rubric and all shadows; only the
active one pushes alerts. Compare them in the report's "Rubric comparison"
section, then promote or retire:

    python -m alerts.rubrics status
    python -m alerts.rubrics promote v2   # v2 becomes active, the old active is retired
    python -m alerts.rubrics retire v2    # stop scoring with a shadow
"""
from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import db
from .config import ROOT

log = logging.getLogger(__name__)

PROMPTS_DIR = ROOT / "prompts"


@dataclass
class Rubric:
    id: str
    model: str
    system_prompt: str
    notes: str = ""
    status: Optional[str] = None


def parse(path: Path) -> Rubric:
    header, sep, body = path.read_text().partition("\n---\n")
    if not sep:
        raise ValueError(f"{path.name}: missing '---' line between header and prompt")
    fields = dict(line.split(":", 1) for line in header.splitlines() if ":" in line)
    fields = {k.strip(): v.strip() for k, v in fields.items()}
    if not fields.get("model"):
        raise ValueError(f"{path.name}: header needs a model: line")
    return Rubric(id=path.stem, model=fields["model"], system_prompt=body.strip("\n"), notes=fields.get("notes", ""))


def load_files(directory: Path = PROMPTS_DIR) -> List[Rubric]:
    paths = [p for p in directory.glob("v*.md") if re.fullmatch(r"v\d+", p.stem)]
    return [parse(p) for p in sorted(paths, key=lambda p: int(p.stem[1:]))]


def sync(conn, directory: Path = PROMPTS_DIR) -> List[str]:
    """Insert rubric files the database doesn't have yet. Returns the new ids."""
    existing = {r.id: r for r in stored(conn)}
    added = []
    for rubric in load_files(directory):
        if rubric.id not in existing:
            db.ensure_prompt_version(conn, rubric.id, rubric.model, rubric.system_prompt, rubric.notes)
            added.append(rubric.id)
        elif existing[rubric.id].system_prompt != rubric.system_prompt or existing[rubric.id].model != rubric.model:
            log.warning("%s.md differs from what was recorded; versions are immutable, so add a new file", rubric.id)
    return added


def stored(conn) -> List[Rubric]:
    return [Rubric(*row) for row in db.prompt_versions(conn)]


def scoring(conn) -> List[Rubric]:
    """The active rubric first, then shadows."""
    rubrics = [r for r in stored(conn) if r.status in ("active", "shadow")]
    rubrics.sort(key=lambda r: (r.status != "active", int(r.id[1:]) if r.id[1:].isdigit() else 0))
    if not rubrics or rubrics[0].status != "active":
        raise RuntimeError("no active rubric in prompt_versions")
    return rubrics


def promote(conn, version: str) -> None:
    by_id = {r.id: r for r in stored(conn)}
    if by_id.get(version) is None or by_id[version].status != "shadow":
        raise ValueError(f"{version} is not a shadow rubric")
    with conn.transaction():  # the unique index allows only one active at a time
        conn.execute("UPDATE prompt_versions SET status = 'retired' WHERE status = 'active'")
        conn.execute("UPDATE prompt_versions SET status = 'active' WHERE id = %s", (version,))


def retire(conn, version: str) -> None:
    by_id = {r.id: r for r in stored(conn)}
    if by_id.get(version) is None or by_id[version].status != "shadow":
        raise ValueError(f"{version} is not a shadow rubric (to replace the active one, promote a shadow)")
    conn.execute("UPDATE prompt_versions SET status = 'retired' WHERE id = %s", (version,))


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage scoring rubrics")
    parser.add_argument("command", choices=["status", "promote", "retire"])
    parser.add_argument("version", nargs="?")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with db.connect() as conn:
        if args.command == "promote":
            promote(conn, args.version)
        elif args.command == "retire":
            retire(conn, args.version)
        for r in stored(conn):
            print(f"{r.id:5} {r.status:8} {r.model}  {r.notes}")


if __name__ == "__main__":
    main()
