"""Every evidence table rejects UPDATE, DELETE and TRUNCATE, enforced in the database.

Uses one finished standard run (plus one resolution) so that every table has at least one
row; a refused no-op on an empty table would prove nothing.
"""

from __future__ import annotations

import pytest

from tests.conftest import APPEND_ONLY, RUN_STATE, raises_sqlstate

# table -> a column to "update" to its own value (even a no-op update must be refused)
EVIDENCE_TABLES = {
    "staff_user": "display_name",
    "import_file": "original_name",
    "import_parse": "parser_version",
    "ledger_entry": "description",
    "settlement_line": "payout_id",
    "bank_statement": "msg_id",
    "bank_entry": "debtor_name",
    "run_match": "explanation",
    "run_exception": "explanation",
    "allocation_ledger": "match_id",
    "allocation_settlement": "match_id",
    "allocation_bank": "match_id",
    "resolution_reason": "label",
    "resolution": "note",
}


@pytest.fixture
def populated(conn, build):
    month, run_id, ids = build.finished_standard_run()
    conn.execute(
        "INSERT INTO resolution (exception_id, reason_code, note, resolved_by) "
        "VALUES (%s, 'other_see_note', 'Synthetic note for the append-only sweep.', %s)",
        (ids[4], month.staff),
    )
    return run_id


@pytest.mark.parametrize("table, column", sorted(EVIDENCE_TABLES.items()))
def test_update_is_refused(conn, populated, table, column):
    assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > 0
    with raises_sqlstate(conn, APPEND_ONLY):
        conn.execute(f"UPDATE {table} SET {column} = {column}")


@pytest.mark.parametrize("table", sorted(EVIDENCE_TABLES))
def test_delete_is_refused(conn, populated, table):
    with raises_sqlstate(conn, APPEND_ONLY):
        conn.execute(f"DELETE FROM {table}")


@pytest.mark.parametrize("table", sorted(EVIDENCE_TABLES) + ["reconciliation_run"])
def test_truncate_is_refused_even_with_cascade(conn, populated, table):
    with raises_sqlstate(conn, APPEND_ONLY):
        conn.execute(f"TRUNCATE {table} CASCADE")


def test_finished_run_cannot_be_updated(conn, populated):
    with raises_sqlstate(conn, RUN_STATE):
        conn.execute("UPDATE reconciliation_run SET engine_git_sha = engine_git_sha")


def test_run_cannot_be_deleted(conn, populated):
    with raises_sqlstate(conn, APPEND_ONLY):
        conn.execute("DELETE FROM reconciliation_run")
