"""Job queue argument rules, and database role privileges (defence in depth under triggers)."""

from __future__ import annotations

import pytest

from tests.conftest import CHECK_VIOLATION, INSUFFICIENT_PRIVILEGE, UNIQUE_VIOLATION, raises_sqlstate


# --- jobs ---------------------------------------------------------------------------------

def test_parse_job_requires_its_file(conn):
    with raises_sqlstate(conn, CHECK_VIOLATION):
        conn.execute("INSERT INTO job (kind) VALUES ('parse_file')")


def test_reconcile_job_requires_all_three_files(conn, build):
    ledger = build.file("ledger")
    with raises_sqlstate(conn, CHECK_VIOLATION):
        conn.execute("INSERT INTO job (kind, ledger_file_id) VALUES ('reconcile', %s)", (ledger,))


def test_job_cannot_carry_arguments_of_another_kind(conn, build):
    file_id = build.file("ledger")
    with raises_sqlstate(conn, CHECK_VIOLATION):
        conn.execute(
            "INSERT INTO job (kind, import_file_id, ledger_file_id) VALUES ('parse_file', %s, %s)",
            (file_id, file_id),
        )


def test_at_most_one_parse_job_per_file(conn, build):
    file_id = build.file("ledger")
    conn.execute("INSERT INTO job (kind, import_file_id) VALUES ('parse_file', %s)", (file_id,))
    with raises_sqlstate(conn, UNIQUE_VIOLATION):
        conn.execute("INSERT INTO job (kind, import_file_id) VALUES ('parse_file', %s)", (file_id,))


def test_failed_job_requires_an_error(conn, build):
    file_id = build.file("ledger")
    (job_id,) = conn.execute(
        "INSERT INTO job (kind, import_file_id) VALUES ('parse_file', %s) RETURNING id", (file_id,)
    ).fetchone()
    with raises_sqlstate(conn, CHECK_VIOLATION):
        conn.execute("UPDATE job SET status = 'failed' WHERE id = %s", (job_id,))


# --- roles --------------------------------------------------------------------------------

@pytest.fixture
def resolved(conn, build):
    month, run_id, ids = build.finished_standard_run()
    (resolution_id,) = conn.execute(
        "INSERT INTO resolution (exception_id, reason_code, note, resolved_by) "
        "VALUES (%s, 'other_see_note', 'Synthetic note written by the owner role.', %s) RETURNING id",
        (ids[4], month.staff),
    ).fetchone()
    return month, ids, resolution_id


def test_web_role_can_insert_a_resolution(conn, resolved):
    month, ids, _ = resolved
    with conn.transaction():
        conn.execute("SET LOCAL ROLE recon_web")
        conn.execute(
            "INSERT INTO resolution (exception_id, reason_code, note, resolved_by) "
            "VALUES (%s, 'other_see_note', 'Synthetic note written by the web role.', %s)",
            (ids[5], month.staff),
        )
    conn.execute("RESET ROLE")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE resolution SET note = note",
        "DELETE FROM resolution",
        "UPDATE import_file SET original_name = original_name",
        "DELETE FROM import_file",
        "UPDATE reconciliation_run SET error = error",
        "INSERT INTO run_match (run_id, ordinal, pass, explanation) VALUES (1, 1, 'exact', 'x')",
        "UPDATE ledger_entry SET description = description",
    ],
)
def test_web_role_lacks_privileges_to_change_evidence(conn, resolved, statement):
    """Refused by privilege (42501) before any trigger runs."""
    with raises_sqlstate(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("SET LOCAL ROLE recon_web")
        conn.execute(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE resolution SET note = note",
        "DELETE FROM resolution",
        "DELETE FROM reconciliation_run",
        "DELETE FROM ledger_entry",
        "SELECT * FROM schema_migration",
    ],
)
def test_worker_role_lacks_privileges_outside_its_job(conn, resolved, statement):
    with raises_sqlstate(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("SET LOCAL ROLE recon_worker")
        conn.execute(statement)
