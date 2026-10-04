"""Requeueing failed jobs (D-074): idempotent, attempts preserved, budget enforced, controller
only, every requeue logged. Enforced in the database, so it holds whatever the web layer does."""

from __future__ import annotations

import threading

import psycopg
import pytest

from recon import importer
from recon.migrate import migrate
from tests.conftest import APPEND_ONLY, fresh_database, raises_sqlstate


@pytest.fixture
def staff(conn):
    ids = importer.seed_synthetic_staff(conn)
    names = dict(conn.execute("SELECT display_name, id FROM staff_user").fetchall())
    return {"analyst": names["Demo Analyst 1"], "controller": names["Demo Controller"], "ids": ids}


def _failed_job(conn, build, attempts: int = 1) -> int:
    file_id = build.file("ledger")
    (job_id,) = conn.execute(
        "INSERT INTO job (kind, import_file_id, status, attempts, error, finished_at) "
        "VALUES ('parse_file', %s, 'failed', %s, 'synthetic: worker crashed', now()) RETURNING id",
        (file_id, attempts)).fetchone()
    return job_id


def _requeue(conn, job_id, staff_id) -> str:
    return conn.execute("SELECT requeue_failed_job(%s, %s)", (job_id, staff_id)).fetchone()[0]


def test_staff_roles_are_seeded(conn, staff):
    assert dict(conn.execute("SELECT display_name, role FROM staff_user").fetchall()) == {
        "Demo Analyst 1": "analyst", "Demo Analyst 2": "analyst", "Demo Controller": "controller"}


def test_requeueing_the_same_failed_job_twice_results_in_one_job(conn, build, staff):
    job_id = _failed_job(conn, build)
    before = conn.execute("SELECT count(*) FROM job").fetchone()[0]
    assert _requeue(conn, job_id, staff["controller"]) == "requeued"
    assert _requeue(conn, job_id, staff["controller"]) == "already_queued"
    assert conn.execute("SELECT count(*) FROM job").fetchone()[0] == before
    assert conn.execute("SELECT status FROM job WHERE id = %s", (job_id,)).fetchone() == ("queued",)
    assert conn.execute("SELECT count(*) FROM job_requeue WHERE job_id = %s", (job_id,)).fetchone() == (1,)


def test_requeue_preserves_attempts(conn, build, staff):
    job_id = _failed_job(conn, build, attempts=2)
    _requeue(conn, job_id, staff["controller"])
    assert conn.execute("SELECT attempts, error FROM job WHERE id = %s", (job_id,)).fetchone() == (2, None)


def test_requeue_logs_who_when_and_the_error_it_replaced(conn, build, staff):
    job_id = _failed_job(conn, build, attempts=1)
    _requeue(conn, job_id, staff["controller"])
    assert conn.execute(
        "SELECT requeued_by, attempts_at_requeue, previous_error FROM job_requeue WHERE job_id = %s",
        (job_id,)).fetchone() == (staff["controller"], 1, "synthetic: worker crashed")


def test_a_job_at_its_attempt_budget_cannot_be_requeued(conn, build, staff):
    (budget,) = conn.execute("SELECT recon_max_job_attempts()").fetchone()
    job_id = _failed_job(conn, build, attempts=budget)
    assert _requeue(conn, job_id, staff["controller"]) == "attempts_exhausted"
    assert conn.execute("SELECT status, attempts FROM job WHERE id = %s", (job_id,)).fetchone() == (
        "failed", budget)


def test_only_failed_jobs_can_be_requeued(conn, build, staff):
    file_id = build.file("ledger")
    (done,) = conn.execute(
        "INSERT INTO job (kind, import_file_id, status, attempts) VALUES ('parse_file', %s, 'done', 1) "
        "RETURNING id", (file_id,)).fetchone()
    assert _requeue(conn, done, staff["controller"]) == "not_failed"
    assert _requeue(conn, 987654321, staff["controller"]) == "not_found"


def test_an_analyst_is_refused_by_the_database(conn, build, staff):
    job_id = _failed_job(conn, build)
    with raises_sqlstate(conn, "RC005"):
        _requeue(conn, job_id, staff["analyst"])
    assert conn.execute("SELECT status FROM job WHERE id = %s", (job_id,)).fetchone() == ("failed",)


def test_the_requeue_log_is_append_only(conn, build, staff):
    _requeue(conn, _failed_job(conn, build), staff["controller"])
    with raises_sqlstate(conn, APPEND_ONLY):
        conn.execute("UPDATE job_requeue SET previous_error = 'rewritten'")


def test_the_worker_never_claims_a_job_at_its_budget(conn, staff):
    """Poison-pill protection: a job that keeps killing its worker stops being claimed."""
    (budget,) = conn.execute("SELECT recon_max_job_attempts()").fetchone()
    stored = importer.store_file(conn, "ledger", "x.csv", b"# SYNTHETIC\nnot,a,ledger\n", staff["ids"][0])
    conn.execute("UPDATE job SET attempts = %s WHERE id = %s", (budget, stored.job_id))
    assert importer.process_parse_job(conn, stored.job_id) is None
    assert conn.execute("SELECT status FROM job WHERE id = %s", (stored.job_id,)).fetchone() == ("queued",)


def test_a_second_active_replay_of_the_same_run_is_a_no_op(conn, build):
    month, run_id, _ = build.finished_standard_run()
    insert = ("INSERT INTO job (kind, replay_of_run_id) VALUES ('replay', %s) "
              "ON CONFLICT (replay_of_run_id) WHERE kind = 'replay' AND status IN ('queued', 'running') "
              "DO NOTHING RETURNING id")
    assert conn.execute(insert, (run_id,)).fetchone() is not None
    assert conn.execute(insert, (run_id,)).fetchone() is None
    assert conn.execute("SELECT count(*) FROM job WHERE kind = 'replay'").fetchone() == (1,)


def test_two_simultaneous_requeues_produce_one():
    with fresh_database() as url:
        migrate(url)
        with psycopg.connect(url, autocommit=True) as conn:
            importer.seed_synthetic_staff(conn)
            (controller,) = conn.execute(
                "SELECT id FROM staff_user WHERE role = 'controller'").fetchone()
            stored = importer.store_file(conn, "ledger", "x.csv", b"# SYNTHETIC\nx\n", controller)
            conn.execute("UPDATE job SET status = 'failed', attempts = 1, error = 'synthetic' "
                         "WHERE id = %s", (stored.job_id,))
        results = []

        def requeue():
            with psycopg.connect(url, autocommit=True) as c:
                results.append(c.execute("SELECT requeue_failed_job(%s, %s)",
                                         (stored.job_id, controller)).fetchone()[0])

        threads = [threading.Thread(target=requeue) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert sorted(results) == ["already_queued"] * 4 + ["requeued"]
        with psycopg.connect(url) as conn:
            assert conn.execute("SELECT count(*) FROM job_requeue").fetchone() == (1,)
