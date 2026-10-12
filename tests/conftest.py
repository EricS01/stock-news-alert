import os
import uuid

import pytest

from alerts import db


@pytest.fixture
def conn():
    """A migrated database in a throwaway schema.

    Needs TEST_DATABASE_URL (a direct, non-pooled connection string: the schema is
    selected with a session-level SET). Skipped without it, unless REQUIRE_DB_TESTS
    is set, as in CI.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        if os.environ.get("REQUIRE_DB_TESTS"):
            pytest.fail("REQUIRE_DB_TESTS is set but TEST_DATABASE_URL is not")
        pytest.skip("TEST_DATABASE_URL not set")
    schema = f"test_{uuid.uuid4().hex[:12]}"
    c = db.connect(url)
    c.execute(f"CREATE SCHEMA {schema}")
    c.execute(f"SET search_path TO {schema}")
    db.migrate(c)
    try:
        yield c
    finally:
        c.execute(f"DROP SCHEMA {schema} CASCADE")
        c.close()
