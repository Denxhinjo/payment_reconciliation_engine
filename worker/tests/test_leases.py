"""Job leases (D-081, closes F19): a claim takes a lease; the sweep fails jobs whose worker died,
attempts kept; a job inside its lease is untouched; a job that keeps dying stops at the budget;
a worker whose lease lapsed cannot record its result."""

from __future__ import annotations

from datetime import timedelta

import psycopg
import pytest

from recon import importer, jobs
from recon.migrate import migrate
from tests.conftest import CHECK_VIOLATION, fresh_database, raises_sqlstate

PARSABLE = b"# SYNTHETIC DEMO DATA\nentry_id,booked_on,entry_type,channel,payment_reference,currency,amount_minor,customer_ref,description\nLE-1,2026-09-01,payment,card,PAY-1,EUR,100,SYN-CUST-0001,Synthetic\n"


@pytest.fixture
def staff(conn):
    importer.seed_synthetic_staff(conn)
    return dict(conn.execute("SELECT display_name, id FROM staff_user").fetchall())


def _queued_parse_job(conn, staff, content: bytes = PARSABLE) -> int:
    return importer.store_file(conn, "ledger", "lease.csv", content, staff["Demo Analyst 1"]).job_id


def _expire(conn, job_id):
    """Pretend the worker holding this job died 16 minutes ago."""
    conn.execute("UPDATE job SET lease_until = now() - interval '1 minute' WHERE id = %s", (job_id,))


def _job(conn, job_id):
    return conn.execute("SELECT status, attempts, error FROM job WHERE id = %s", (job_id,)).fetchone()


def test_a_claim_takes_a_fifteen_minute_lease(conn, staff):
    job_id = _queued_parse_job(conn, staff)
    claimed = jobs.claim(conn, "parse_file", "import_file_id", job_id)
    (now,) = conn.execute("SELECT now()").fetchone()
    assert claimed.lease_until - now == timedelta(minutes=15)
    assert _job(conn, job_id)[:2] == ("running", 1)


def test_a_stale_running_job_is_reclaimed_as_failed_with_attempts_preserved(conn, staff):
    job_id = _queued_parse_job(conn, staff)
    jobs.claim(conn, "parse_file", "import_file_id", job_id)
    _expire(conn, job_id)
    assert jobs.expire_leases(conn) == [job_id]
    assert _job(conn, job_id) == ("failed", 1, "lease expired: worker did not finish")


def test_a_job_within_its_lease_is_untouched(conn, staff):
    job_id = _queued_parse_job(conn, staff)
    jobs.claim(conn, "parse_file", "import_file_id", job_id)
    assert jobs.expire_leases(conn) == []
    assert _job(conn, job_id) == ("running", 1, None)


def test_the_sweep_ignores_jobs_that_are_not_running(conn, staff, build):
    queued = _queued_parse_job(conn, staff)
    done = _queued_parse_job(conn, staff, PARSABLE + b"LE-2,2026-09-01,payment,card,PAY-2,EUR,100,SYN-CUST-0001,Synthetic\n")
    importer.process_parse_job(conn, done)
    conn.execute("UPDATE job SET lease_until = now() - interval '1 hour' WHERE id IN (%s, %s)", (queued, done))
    assert jobs.expire_leases(conn) == []
    assert _job(conn, queued)[0] == "queued" and _job(conn, done)[0] == "done"


def test_a_job_that_repeatedly_expires_stops_at_the_attempt_budget(conn, staff):
    (budget,) = conn.execute("SELECT recon_max_job_attempts()").fetchone()
    job_id = _queued_parse_job(conn, staff)
    controller = staff["Demo Controller"]
    for attempt in range(1, budget + 1):
        claimed = jobs.claim(conn, "parse_file", "import_file_id", job_id)
        assert claimed is not None, f"attempt {attempt} should be claimable"
        _expire(conn, job_id)                                   # the worker dies again
        assert jobs.expire_leases(conn) == [job_id]
        assert _job(conn, job_id) == ("failed", attempt, "lease expired: worker did not finish")
        outcome = conn.execute("SELECT requeue_failed_job(%s, %s)", (job_id, controller)).fetchone()[0]
        assert outcome == ("requeued" if attempt < budget else "attempts_exhausted")
    # At the budget: requeue refused (above), and the worker will not claim it even if queued.
    assert _job(conn, job_id)[:2] == ("failed", budget)
    conn.execute("UPDATE job SET status = 'queued', error = NULL WHERE id = %s", (job_id,))
    assert jobs.claim(conn, "parse_file", "import_file_id", job_id) is None


def test_a_running_job_must_have_a_lease(conn, staff):
    job_id = _queued_parse_job(conn, staff)
    with raises_sqlstate(conn, CHECK_VIOLATION):
        conn.execute("UPDATE job SET status = 'running' WHERE id = %s", (job_id,))


def test_finishing_after_the_lease_was_swept_is_refused(conn, staff):
    job_id = _queued_parse_job(conn, staff)
    claimed = jobs.claim(conn, "parse_file", "import_file_id", job_id)
    _expire(conn, job_id)
    jobs.expire_leases(conn)
    with pytest.raises(jobs.LeaseLost):
        with conn.transaction():
            jobs.finish(conn, claimed, "done")
    assert _job(conn, job_id)[0] == "failed"


def test_a_worker_whose_lease_lapsed_mid_parse_commits_nothing(monkeypatch):
    """Two real connections: while worker A is parsing, its lease lapses and the sweep (run by
    another worker) fails the job. A's parse rows must roll back, not be committed."""
    with fresh_database() as url:
        migrate(url)
        with psycopg.connect(url, autocommit=True) as setup:
            importer.seed_synthetic_staff(setup)
            (staff_id,) = setup.execute("SELECT id FROM staff_user LIMIT 1").fetchone()
            job_id = importer.store_file(setup, "ledger", "lease.csv", PARSABLE, staff_id).job_id

        real_insert = importer._insert_rows

        def insert_then_lose_lease(conn, file_id, kind, raw):
            rows = real_insert(conn, file_id, kind, raw)
            with psycopg.connect(url, autocommit=True) as other_worker:
                other_worker.execute("UPDATE job SET lease_until = now() - interval '1 minute' WHERE id = %s",
                                     (job_id,))
                assert jobs.expire_leases(other_worker) == [job_id]
            return rows

        monkeypatch.setattr(importer, "_insert_rows", insert_then_lose_lease)
        with psycopg.connect(url, autocommit=True) as worker_a:
            with pytest.raises(jobs.LeaseLost):
                importer.process_parse_job(worker_a, job_id)
        with psycopg.connect(url) as check:
            assert check.execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)
            assert check.execute("SELECT count(*) FROM import_parse").fetchone() == (0,)
            assert check.execute("SELECT status, attempts, error FROM job WHERE id = %s", (job_id,)).fetchone() == (
                "failed", 1, "lease expired: worker did not finish")


def test_every_worker_invocation_sweeps_first(capsys):
    from recon.__main__ import main

    with fresh_database() as url:
        migrate(url)
        with psycopg.connect(url, autocommit=True) as conn:
            importer.seed_synthetic_staff(conn)
            (staff_id,) = conn.execute("SELECT id FROM staff_user LIMIT 1").fetchone()
            job_id = importer.store_file(conn, "ledger", "lease.csv", PARSABLE, staff_id).job_id
            jobs.claim(conn, "parse_file", "import_file_id", job_id)          # a worker claims it...
            _expire(conn, job_id)                                             # ...and dies
        assert main(["worker", "--database-url", url]) == 0
        assert f"job #{job_id}: lease expired: worker did not finish; marked failed, attempts kept" in \
            capsys.readouterr().out
        with psycopg.connect(url) as conn:
            assert _job(conn, job_id) == ("failed", 1, "lease expired: worker did not finish")


@pytest.mark.parametrize("role, allowed", [("recon_worker", True), ("recon_web", False), ("public", False)])
def test_only_the_worker_may_sweep(conn, role, allowed):
    (granted,) = conn.execute(
        "SELECT has_function_privilege(%s, 'expire_job_leases()', 'EXECUTE')", (role,)).fetchone()
    assert granted is allowed
