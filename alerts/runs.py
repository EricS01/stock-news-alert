"""Bookkeeping shared by every job: each run is one row in the `runs` table."""
from __future__ import annotations

import logging
import time
from typing import Callable

from . import db

log = logging.getLogger(__name__)


class RunFailed(RuntimeError):
    """A run that should show red in Actions. args: (message, counts-so-far)."""


def record_run(conn, job: str, work: Callable[[], dict]) -> dict:
    """Run `work` inside a `runs` row: ok with its counts, or failed with the error."""
    started = time.monotonic()
    run_id = db.start_run(conn, job)
    try:
        counts = work()
    except Exception as e:
        counts = e.args[1] if isinstance(e, RunFailed) and len(e.args) > 1 else {}
        counts["duration_s"] = round(time.monotonic() - started, 1)
        db.finish_run(conn, run_id, "failed", counts, error=str(e.args[0]) if e.args else repr(e))
        raise
    counts["duration_s"] = round(time.monotonic() - started, 1)
    log.info("run counts: %s", counts)
    db.finish_run(conn, run_id, "ok", counts)
    return counts
