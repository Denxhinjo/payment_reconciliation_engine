"""Shared fixtures. Every database test runs against real PostgreSQL (D-027).

Set RECON_TEST_ADMIN_URL to a superuser connection string for a disposable server, e.g.
    postgresql://postgres:recon_test@127.0.0.1:54329/postgres
Each test session creates its own database, applies the real migrations with the real
runner, and drops the database at the end. Each test runs inside a transaction that is
rolled back, so tests cannot affect one another.

If RECON_TEST_ADMIN_URL is missing, database tests FAIL rather than skip: a skipped
constraint test looks like a passing one in a summary, and that is exactly the wrong
failure mode for this project.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from typing import Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from recon.migrate import migrate
from tests.factories import Builder


def _admin_url() -> str:
    url = os.environ.get("RECON_TEST_ADMIN_URL")
    if not url:
        pytest.fail(
            "RECON_TEST_ADMIN_URL is not set. Database tests need a disposable PostgreSQL "
            "server; see README.md 'Running the tests'.",
            pytrace=False,
        )
    return url


@contextmanager
def fresh_database() -> Iterator[str]:
    """Create an empty, uniquely named database; yield its URL; drop it afterwards."""
    admin = _admin_url()
    name = f"recon_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(admin, dbname=name)
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def empty_database_url() -> Iterator[str]:
    """A brand-new database with no migrations applied (for migration-runner tests)."""
    with fresh_database() as url:
        yield url


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """One migrated database shared by the session; tests isolate via rollback."""
    with fresh_database() as url:
        migrate(url)
        yield url


@pytest.fixture
def conn(database_url: str) -> Iterator[psycopg.Connection]:
    """A connection inside a transaction that is always rolled back."""
    with psycopg.connect(database_url) as connection:
        try:
            yield connection
        finally:
            connection.rollback()


@pytest.fixture
def build(conn: psycopg.Connection) -> Builder:
    return Builder(conn)


@contextmanager
def raises_sqlstate(conn: psycopg.Connection, sqlstate: str) -> Iterator[None]:
    """Assert the block fails with exactly ``sqlstate``.

    The block runs in a savepoint, so the surrounding test transaction stays usable.
    Asserting the SQLSTATE (not just "some error") proves the *intended* constraint fired:
    a test for a CHECK must not pass because of an unrelated foreign-key error.
    """
    with pytest.raises(psycopg.Error) as info:
        with conn.transaction():
            yield
    assert info.value.sqlstate == sqlstate, (
        f"expected SQLSTATE {sqlstate}, got {info.value.sqlstate}: {info.value}"
    )


# Standard SQLSTATEs used in assertions.
UNIQUE_VIOLATION = "23505"
CHECK_VIOLATION = "23514"
FOREIGN_KEY_VIOLATION = "23503"
NOT_NULL_VIOLATION = "23502"
GENERATED_ALWAYS = "428C9"
INSUFFICIENT_PRIVILEGE = "42501"
# Project SQLSTATEs (see migrations 0001, 0003, 0004).
APPEND_ONLY = "RC001"
RUN_STATE = "RC002"
FINISH_CHECK = "RC003"
RUN_NOT_FINISHED = "RC004"
