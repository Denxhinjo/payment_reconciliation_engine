"""Every job records who asked for it; seeded data belongs to a system actor; the demo seed goes
through the real job path and fails closed (D-091, D-092)."""

from __future__ import annotations

import shutil
from pathlib import Path

import psycopg
import pytest

from recon import importer, jobs, runs, seed
from recon.migrate import DEFAULT_MIGRATIONS_DIR, migrate
from tests.conftest import CHECK_VIOLATION, NOT_NULL_VIOLATION, fresh_database, raises_sqlstate

REQUESTER_CHANGED = "RC006"
REQUESTER_UNKNOWN = "RC007"
MIGRATION_0012 = DEFAULT_MIGRATIONS_DIR / "0012_job_requester.sql"
DEMO_DIR = Path(__file__).resolve().parents[2] / "demo-data" / "2026-09"
DEMO_PEOPLE = ("Demo Analyst 1", "Demo Analyst 2", "Demo Controller")


@pytest.fixture
def staff(conn):
    importer.seed_synthetic_staff(conn)
    return dict(conn.execute("SELECT display_name, id FROM staff_user").fetchall())


def _requester(conn, job_id: int) -> int:
    return conn.execute("SELECT requested_by FROM job WHERE id = %s", (job_id,)).fetchone()[0]


# --- the column and its rules --------------------------------------------------------------------

def test_a_job_without_a_requester_is_refused(conn, build):
    file_id = build.file("ledger")
    with raises_sqlstate(conn, NOT_NULL_VIOLATION):
        conn.execute("INSERT INTO job (kind, import_file_id) VALUES ('parse_file', %s)", (file_id,))


def test_the_requester_cannot_be_changed_by_anyone(conn, staff):
    stored = importer.store_file(conn, "ledger", "x.csv", b"# SYNTHETIC requester\n", staff["Demo Analyst 1"])
    with raises_sqlstate(conn, REQUESTER_CHANGED):
        conn.execute("UPDATE job SET requested_by = %s WHERE id = %s",
                     (staff["Demo Controller"], stored.job_id))


def test_an_upload_s_parse_job_records_the_uploader(conn, staff):
    uploader = staff["Demo Analyst 2"]
    stored = importer.store_file(conn, "ledger", "x.csv", b"# SYNTHETIC uploader\n", uploader)
    assert stored.created and _requester(conn, stored.job_id) == uploader
    again = importer.store_file(conn, "ledger", "y.csv", b"# SYNTHETIC uploader\n", staff["Demo Controller"])
    assert (again.created, again.job_id, again.file_id) == (False, None, stored.file_id)
    assert conn.execute("SELECT count(*) FROM job WHERE import_file_id = %s",
                        (stored.file_id,)).fetchone() == (1,)


def test_enqueued_jobs_keep_their_requester_through_the_worker(conn, staff):
    """The requester recorded at enqueue survives claim, attempt, run and done unchanged."""
    analyst, controller = staff["Demo Analyst 1"], staff["Demo Controller"]
    files = {}
    for kind, name in seed.DEMO_FILES:
        stored = importer.store_file(conn, kind, name, (DEMO_DIR / name).read_bytes(), analyst)
        importer.process_parse_job(conn, stored.job_id)
        assert _requester(conn, stored.job_id) == analyst
        files[kind] = stored.file_id
    reconcile = jobs.enqueue_reconcile(conn, files["ledger"], files["settlement"], files["bank"], controller)
    assert runs.process_reconcile_job(conn, reconcile)[1].status == "finished"
    run_id = conn.execute("SELECT run_id FROM job WHERE id = %s", (reconcile,)).fetchone()[0]
    replay = jobs.enqueue_replay(conn, run_id, analyst)
    assert runs.process_replay_job(conn, replay)[1].identical
    rows = conn.execute("SELECT id, status, attempts, requested_by FROM job WHERE id IN (%s, %s) ORDER BY id",
                        (reconcile, replay)).fetchall()
    assert rows == [(reconcile, "done", 1, controller), (replay, "done", 1, analyst)]
    assert conn.execute("SELECT requested_by_name, requested_by_role FROM run_overview WHERE run_id = %s",
                        (run_id,)).fetchone() == ("Demo Controller", "controller")


def test_a_second_pending_request_is_a_no_op_and_keeps_the_first_requester(conn, build, staff):
    month, run_id, _ = build.finished_standard_run()
    first = jobs.enqueue_replay(conn, run_id, staff["Demo Analyst 1"])
    assert jobs.enqueue_replay(conn, run_id, staff["Demo Controller"]) is None
    assert _requester(conn, first) == staff["Demo Analyst 1"]


@pytest.mark.parametrize("function", ["upload_file(file_kind, text, bytea, bigint)",
                                      "enqueue_reconcile(bigint, bigint, bigint, bigint)",
                                      "enqueue_replay(bigint, bigint)"])
@pytest.mark.parametrize("role, allowed", [("recon_web", True), ("recon_worker", True), ("public", False)])
def test_only_the_application_roles_may_enqueue(conn, function, role, allowed):
    (granted,) = conn.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, function)).fetchone()
    assert granted is allowed


# --- the system actor ----------------------------------------------------------------------------

def test_the_system_actor_exists_once_and_is_the_only_system_user(conn):
    assert conn.execute("SELECT display_name, is_synthetic FROM staff_user WHERE role = 'system'"
                        ).fetchall() == [(seed.SYSTEM_ACTOR, True)]


def test_staff_roles_are_still_a_closed_list(conn):
    with raises_sqlstate(conn, CHECK_VIOLATION):
        conn.execute("INSERT INTO staff_user (display_name, role) VALUES ('Synthetic Admin', 'admin')")


# --- migration 0012 ------------------------------------------------------------------------------

def test_migration_0012_is_safe_to_run_twice(conn, staff):
    stored = importer.store_file(conn, "ledger", "x.csv", b"# SYNTHETIC twice\n", staff["Demo Analyst 1"])
    conn.execute(MIGRATION_0012.read_text(encoding="utf-8"))
    assert conn.execute("SELECT count(*) FROM staff_user WHERE role = 'system'").fetchone() == (1,)
    assert _requester(conn, stored.job_id) == staff["Demo Analyst 1"]
    with raises_sqlstate(conn, REQUESTER_CHANGED):
        conn.execute("UPDATE job SET requested_by = %s WHERE id = %s", (staff["Demo Controller"], stored.job_id))


def _migrated_to_0011(url: str, tmp_path: Path) -> None:
    earlier = tmp_path / "migrations"
    earlier.mkdir()
    for path in DEFAULT_MIGRATIONS_DIR.glob("*.sql"):
        if path.name < "0012":
            shutil.copyfile(path, earlier / path.name)
    migrate(url, earlier)


def test_migration_0012_backfills_parse_jobs_from_the_uploader(tmp_path):
    with fresh_database() as url:
        _migrated_to_0011(url, tmp_path)
        with psycopg.connect(url, autocommit=True) as conn:
            (uploader,) = conn.execute("INSERT INTO staff_user (display_name, role) "
                                       "VALUES ('Demo Analyst 2', 'analyst') RETURNING id").fetchone()
            (file_id,) = conn.execute("INSERT INTO import_file (kind, original_name, raw, uploaded_by) "
                                      "VALUES ('ledger', 'x.csv', '\\x00'::bytea, %s) RETURNING id",
                                      (uploader,)).fetchone()
            (job_id,) = conn.execute("INSERT INTO job (kind, import_file_id) VALUES ('parse_file', %s) "
                                     "RETURNING id", (file_id,)).fetchone()
        migrate(url)
        with psycopg.connect(url) as conn:
            assert _requester(conn, job_id) == uploader


def test_migration_0012_refuses_to_invent_a_requester(tmp_path):
    """A queued reconcile from before 0012 has no knowable requester: the migration stops."""
    with fresh_database() as url:
        _migrated_to_0011(url, tmp_path)
        with psycopg.connect(url, autocommit=True) as conn:
            (uploader,) = conn.execute("INSERT INTO staff_user (display_name, role) "
                                       "VALUES ('Demo Analyst 2', 'analyst') RETURNING id").fetchone()
            ids = [conn.execute("INSERT INTO import_file (kind, original_name, raw, uploaded_by) "
                                "VALUES (%s, 'x', %s, %s) RETURNING id", (kind, kind.encode(), uploader)).fetchone()[0]
                   for kind in ("ledger", "settlement", "bank")]
            conn.execute("INSERT INTO job (kind, ledger_file_id, settlement_file_id, bank_file_id) "
                         "VALUES ('reconcile', %s, %s, %s)", ids)
        with pytest.raises(psycopg.Error) as refused:
            migrate(url)
        assert refused.value.sqlstate == REQUESTER_UNKNOWN
        with psycopg.connect(url) as conn:
            assert conn.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'job' "
                                "AND column_name = 'requested_by'").fetchone() == (0,)


# --- the demo seed -------------------------------------------------------------------------------

@pytest.fixture
def migrated_url():
    with fresh_database() as url:
        migrate(url)
        yield url


def _counts(url: str) -> dict[str, int]:
    with psycopg.connect(url) as conn:
        return {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("import_file", "job", "reconciliation_run", "run_match", "run_exception")}


def test_the_seed_goes_through_the_job_path_as_the_system_actor(migrated_url):
    golden = seed.golden_sha256()
    with psycopg.connect(migrated_url, autocommit=True) as conn:
        report = seed.seed_demo(conn, expected_sha256=golden, engine_git_sha="synthetic-sha")
    assert report.run_sha256 == golden == report.replay_sha256
    assert (report.run_id, report.replay_run_id, report.matches, report.exceptions) == (1, 2, 65, 7)

    with psycopg.connect(migrated_url) as conn:
        jobs_seen = conn.execute(
            "SELECT kind, status, attempts, requested_by_name, requested_by_role, run_id "
            "FROM job_overview ORDER BY id").fetchall()
        assert jobs_seen == [
            ("parse_file", "done", 1, seed.SYSTEM_ACTOR, "system", None),
            ("parse_file", "done", 1, seed.SYSTEM_ACTOR, "system", None),
            ("parse_file", "done", 1, seed.SYSTEM_ACTOR, "system", None),
            ("reconcile", "done", 1, seed.SYSTEM_ACTOR, "system", 1),
            ("replay", "done", 1, seed.SYSTEM_ACTOR, "system", 2),
        ]
        assert conn.execute(
            "SELECT run_id, status, result_sha256, replay_outcome, matches, exceptions_open, requested_by_name "
            "FROM run_overview ORDER BY run_id").fetchall() == [
            (1, "finished", golden, None, 65, 7, seed.SYSTEM_ACTOR),
            (2, "finished", golden, "identical", 65, 7, seed.SYSTEM_ACTOR),
        ]
        # No seeded record names a demo person: files and jobs belong to the system actor.
        named = conn.execute(
            "SELECT count(*) FROM staff_user s WHERE s.display_name = ANY(%s) AND ("
            " EXISTS (SELECT 1 FROM import_file f WHERE f.uploaded_by = s.id) OR"
            " EXISTS (SELECT 1 FROM job j WHERE j.requested_by = s.id) OR"
            " EXISTS (SELECT 1 FROM resolution r WHERE r.resolved_by = s.id))",
            (list(DEMO_PEOPLE),)).fetchone()
        assert named == (0,)
        # The demo people still exist to sign in as.
        assert {n for (n,) in conn.execute("SELECT display_name FROM staff_user WHERE role <> 'system'")} \
            == set(DEMO_PEOPLE)


def test_the_seed_refuses_a_database_that_is_not_empty(migrated_url):
    golden = seed.golden_sha256()
    with psycopg.connect(migrated_url, autocommit=True) as conn:
        seed.seed_demo(conn, expected_sha256=golden)
        before = _counts(migrated_url)
        with pytest.raises(seed.SeedFailed, match="already has"):
            seed.seed_demo(conn, expected_sha256=golden)
    assert _counts(migrated_url) == before


def test_a_hash_that_differs_from_the_golden_value_fails_the_seed_and_keeps_nothing(migrated_url):
    with psycopg.connect(migrated_url, autocommit=True) as conn:
        with pytest.raises(seed.SeedFailed, match="differs from the golden"):
            seed.seed_demo(conn, expected_sha256="0" * 64)
    assert _counts(migrated_url) == dict.fromkeys(_counts(migrated_url), 0)


def test_a_failure_after_run_1_finished_fails_the_seed_and_leaves_no_run_or_job_done(migrated_url, monkeypatch):
    def crash(*args, **kwargs):
        raise RuntimeError("synthetic crash while replaying")

    monkeypatch.setattr(runs, "process_replay_job", crash)
    with psycopg.connect(migrated_url, autocommit=True) as conn:
        with pytest.raises(RuntimeError, match="synthetic crash"):
            seed.seed_demo(conn, expected_sha256=seed.golden_sha256())
    assert _counts(migrated_url) == dict.fromkeys(_counts(migrated_url), 0)


def test_the_seed_command_prints_ready_only_on_success(migrated_url, capsys, monkeypatch):
    from recon.__main__ import main

    monkeypatch.setattr(seed, "golden_sha256", lambda: "f" * 64)   # a deliberately wrong expectation
    assert main(["seed-demo", "--database-url", migrated_url]) == 1
    failed = capsys.readouterr()
    assert "SEED FAILED, nothing kept" in failed.err and "READY" not in failed.out
    assert _counts(migrated_url)["reconciliation_run"] == 0

    monkeypatch.undo()
    assert main(["seed-demo", "--database-url", migrated_url]) == 0
    out = capsys.readouterr().out
    golden = seed.golden_sha256()
    assert f"golden result sha256 {golden}" in out and f"run    result sha256 {golden}" in out
    assert out.rstrip().endswith("READY")
