"""Plain queue status (D-088) and the nightly reset's pristine check (D-086).

Database side: when a queued job counts as overdue (12 hours, decided by job_overview, D-095), that
a requeue restarts the clock, and that the UI promises no clock time the scheduler cannot keep.
Workflow side: the verification script inside reset-demo.yml, run for real against a seeded
pristine database, passes there and fails otherwise.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import psycopg
import pytest

from recon import importer, runs
from recon.migrate import migrate
from tests.conftest import fresh_database

REPO = Path(__file__).resolve().parents[2]
MESSAGES = (REPO / "web" / "src" / "lib" / "messages.ts").read_text(encoding="utf-8")
DEMO = REPO / "demo-data" / "2026-09"


@pytest.fixture
def staff(conn):
    importer.seed_synthetic_staff(conn)
    return dict(conn.execute("SELECT display_name, id FROM staff_user").fetchall())


def _overdue(conn, job_id) -> bool:
    return conn.execute("SELECT overdue FROM job_overview WHERE id = %s", (job_id,)).fetchone()[0]


def _queued_job(conn, staff, minutes_ago: int, status: str = "queued") -> int:
    stored = importer.store_file(conn, "ledger", f"q{minutes_ago}{status}.csv",
                                 f"# SYNTHETIC {minutes_ago} {status}\n".encode(), staff["Demo Analyst 1"])
    conn.execute("UPDATE job SET queued_at = now() - make_interval(mins => %s) WHERE id = %s",
                 (minutes_ago, stored.job_id))
    if status != "queued":
        conn.execute("UPDATE job SET status = %s, attempts = 1, lease_until = now() + interval '1 minute', "
                     "error = CASE WHEN %s = 'failed' THEN 'synthetic' END WHERE id = %s",
                     (status, status, stored.job_id))
    return stored.job_id


# --- overdue is decided by the database -------------------------------------------------------------

@pytest.mark.parametrize("minutes, overdue", [(0, False), (71, False), (719, False), (721, True), (2000, True)])
def test_a_queued_job_is_overdue_after_twelve_hours(conn, staff, minutes, overdue):
    assert _overdue(conn, _queued_job(conn, staff, minutes)) is overdue


@pytest.mark.parametrize("status", ["running", "failed"])
def test_only_queued_jobs_can_be_overdue(conn, staff, status):
    assert _overdue(conn, _queued_job(conn, staff, 2000, status)) is False


def test_a_requeue_restarts_the_queue_clock(conn, staff):
    job_id = _queued_job(conn, staff, 2000, "failed")
    conn.execute("SELECT requeue_failed_job(%s, %s)", (job_id, staff["Demo Controller"]))
    assert conn.execute("SELECT status FROM job WHERE id = %s", (job_id,)).fetchone() == ("queued",)
    assert _overdue(conn, job_id) is False


def test_the_overdue_threshold_is_twelve_hours_as_the_ui_says(conn):
    (threshold,) = conn.execute("SELECT extract(epoch FROM recon_queue_overdue_after())::int").fetchone()
    assert threshold == 12 * 3600
    assert "Queued for over 12 hours" in MESSAGES


def test_the_ui_promises_no_clock_time_for_the_worker():
    """GitHub delays and drops scheduled runs (D-095), so no message may name a time or frequency
    the scheduler does not keep."""
    for promise in ("minutes past", "every hour", "hourly", "at 03:00"):
        assert promise not in MESSAGES, promise
    layout = (REPO / "web" / "src" / "app" / "layout.tsx").read_text(encoding="utf-8")
    assert "scheduled for 03:00 UTC but often hours later" in layout


# --- the reset workflow's verification script, run for real -----------------------------------------

def _reset_check_script() -> str:
    workflow = (REPO / ".github" / "workflows" / "reset-demo.yml").read_text(encoding="utf-8")
    body = re.search(r"python - <<'PY'\n(.*?)\n\s*PY\n", workflow, re.S).group(1)
    return textwrap.dedent(body)


def _run_check(url: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", _reset_check_script()], cwd=REPO / "worker",
                          env=dict(os.environ, DATABASE_URL=url), capture_output=True, text=True, timeout=120)


@pytest.fixture(scope="module")
def pristine_url():
    """A database prepared exactly as deploy.md steps 2-6 prepare `pristine`."""
    with fresh_database() as url:
        migrate(url)
        with psycopg.connect(url, autocommit=True) as conn:
            importer.seed_synthetic_staff(conn)
            staff_id = importer.staff_id_by_name(conn, "Demo Analyst 1")
            ids = {}
            for kind, name in (("ledger", "synthetic_ledger_2026-09.csv"),
                               ("settlement", "synthetic_orrery_settlement_2026-09.csv"),
                               ("bank", "synthetic_bank_camt053_2026-09.xml")):
                stored = importer.store_file(conn, kind, name, (DEMO / name).read_bytes(), staff_id)
                importer.process_parse_job(conn, stored.job_id)
                ids[kind] = stored.file_id
            run = runs.create_and_run(conn, ids["ledger"], ids["settlement"], ids["bank"])
            runs.replay(conn, run.run_id)
        yield url


def test_reset_check_passes_on_the_prepared_demo_including_the_replay_proof(pristine_url):
    result = _run_check(pristine_url)
    assert result.returncode == 0, result.stderr
    assert "run #2 replay identical" in result.stdout and "live is the pristine demo" in result.stdout


def test_reset_check_fails_if_a_visitor_write_survived(pristine_url):
    with fresh_database() as url:
        migrate(url)
        with psycopg.connect(url, autocommit=True) as conn:
            importer.seed_synthetic_staff(conn)
        result = _run_check(url)          # an empty database: no run #1, no replay
        assert result.returncode != 0 and "NOT the pristine demo" in result.stderr
    with psycopg.connect(pristine_url, autocommit=True) as conn:
        (exception_id,) = conn.execute("SELECT id FROM run_exception WHERE run_id = 1 LIMIT 1").fetchone()
        (staff_id,) = conn.execute("SELECT id FROM staff_user LIMIT 1").fetchone()
        conn.execute("INSERT INTO resolution (exception_id, reason_code, note, resolved_by) VALUES "
                     "(%s, 'other_see_note', 'Synthetic: a visitor resolution that must not survive.', %s)",
                     (exception_id, staff_id))
    result = _run_check(pristine_url)
    assert result.returncode != 0 and "NOT the pristine demo" in result.stderr
