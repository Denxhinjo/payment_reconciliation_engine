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


# --- roles: exhaustive catalog check (D-039) ----------------------------------------------
#
# Checked with has_table_privilege(), which needs no role membership, so this runs the same
# on a local superuser and on Neon (where the deploying owner may not SET ROLE, see D-039).

PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")

# Every privilege each role holds. Anything not listed must be absent.
WEB_WRITES = {"import_file", "job", "resolution"}
WORKER_UPDATES = {"reconciliation_run", "job"}
RUNNER_ONLY = {"schema_migration"}


VIEWS = {"current_resolution", "exception_queue", "run_overview"}   # read-only for every role
# Written only through requeue_failed_job() (SECURITY DEFINER), never directly (D-074).
FUNCTION_WRITTEN = {"job_requeue"}


def _expected(role: str, relation: str) -> set[str]:
    if relation in RUNNER_ONLY:
        return set()
    if relation in VIEWS or relation in FUNCTION_WRITTEN:
        return {"SELECT"}
    if role == "recon_web":
        return {"SELECT"} | ({"INSERT"} if relation in WEB_WRITES else set())
    if role == "recon_worker":
        return {"SELECT", "INSERT"} | ({"UPDATE"} if relation in WORKER_UPDATES else set())
    raise AssertionError(role)


@pytest.mark.parametrize("role", ["recon_web", "recon_worker"])
def test_role_privileges_are_exactly_the_intended_matrix(conn, role):
    relations = [
        name for (name,) in conn.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v') ORDER BY c.relname"
        )
    ]
    assert {"resolution", "current_resolution", "exception_queue"} <= set(relations)
    assert {r for r in relations if r in VIEWS} == VIEWS

    differences = []
    for relation in relations:
        actual = {
            privilege for privilege in PRIVILEGES
            if conn.execute(
                "SELECT has_table_privilege(%s, %s, %s)", (role, f"public.{relation}", privilege)
            ).fetchone()[0]
        }
        expected = _expected(role, relation)
        if actual != expected:
            differences.append(
                f"{relation}: extra {sorted(actual - expected)}, missing {sorted(expected - actual)}"
            )
    assert differences == [], f"{role} privileges differ from the intended matrix:\n" + "\n".join(differences)


@pytest.mark.parametrize("role", ["recon_web", "recon_worker"])
def test_roles_cannot_log_in_and_are_not_privileged(conn, role):
    flags = conn.execute(
        "SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls "
        "FROM pg_roles WHERE rolname = %s",
        (role,),
    ).fetchone()
    assert flags == (False, False, False, False, False)


# --- roles: end-to-end, where the test connection may SET ROLE (D-039) ----------------------

@pytest.fixture
def resolved(conn, build):
    month, run_id, ids = build.finished_standard_run()
    (resolution_id,) = conn.execute(
        "INSERT INTO resolution (exception_id, reason_code, note, resolved_by) "
        "VALUES (%s, 'other_see_note', 'Synthetic note written by the owner role.', %s) RETURNING id",
        (ids[4], month.staff),
    ).fetchone()
    return month, ids, resolution_id


@pytest.fixture
def web_role(conn):
    """Switch to recon_web for the test body.

    Skips only when this server forbids the test connection to SET ROLE (a non-superuser
    owner on PostgreSQL 16+, e.g. Neon). The same guarantee is covered there by the catalog
    test above, so the skip hides no untested property (D-039).
    """
    (can_set,) = conn.execute("SELECT pg_has_role(current_user, 'recon_web', 'SET')").fetchone()
    if not can_set:
        pytest.skip("test connection may not SET ROLE recon_web; covered by the catalog test")
    conn.execute("SET LOCAL ROLE recon_web")
    yield
    conn.execute("RESET ROLE")


def test_web_role_can_insert_a_resolution_end_to_end(conn, resolved, web_role):
    month, ids, _ = resolved
    conn.execute(
        "INSERT INTO resolution (exception_id, reason_code, note, resolved_by) "
        "VALUES (%s, 'other_see_note', 'Synthetic note written by the web role.', %s)",
        (ids[5], month.staff),
    )


def test_web_role_update_is_refused_by_privilege_end_to_end(conn, resolved, web_role):
    """The role switch happened in the fixture, outside the asserted block, so the 42501 can
    only come from the UPDATE itself."""
    with raises_sqlstate(conn, INSUFFICIENT_PRIVILEGE):
        conn.execute("UPDATE resolution SET note = note")


@pytest.mark.parametrize("role, allowed", [("recon_web", True), ("recon_worker", False), ("public", False)])
def test_only_the_web_role_may_execute_requeue(conn, role, allowed):
    (granted,) = conn.execute(
        "SELECT has_function_privilege(%s, 'requeue_failed_job(bigint, bigint)', 'EXECUTE')",
        (role,)).fetchone()
    assert granted is allowed
