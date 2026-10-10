"""Web UI flows (stage 7): the demo banner everywhere, failures visible and distinguished, figures
taken from the database, replay as a job, idempotent requeue, append-only resolutions, idempotent
upload. Real HTTP against the production build."""

from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import unquote_plus

import pytest

WEB_SRC = Path(__file__).resolve().parents[2] / "web" / "src"
BANNER = "DEMO — SYNTHETIC DATA"
RESET_NOTICE = "This demo resets once a day, usually around 03:00 UTC; anything you change is discarded then."


@pytest.fixture(scope="module")
def analyst(webapp):
    return webapp.sign_in("Demo Analyst 1")


@pytest.fixture(scope="module")
def controller(webapp):
    return webapp.sign_in("Demo Controller")


_VIEWER: dict = {}


def _flash(webapp, response) -> str:
    """Follow a 303 back to its page and return the message the page RENDERS for the code in the
    URL. This tests the whole path: route -> code -> fixed text on screen (D-082)."""
    assert response.status == 303, response
    location = response.location
    path = location[location.index("/", location.index("//") + 2):] if "//" in location else location
    assert "notice=" in path or "error=" in path, path
    if "viewer" not in _VIEWER:
        _VIEWER["viewer"] = webapp.sign_in("Demo Analyst 2")
    page = webapp.get(path, cookie=_VIEWER["viewer"])
    shown = re.findall(r'<p class="flash (?:ok|bad)" role="(?:status|alert)">(.*?)</p>', page.text)
    return html.unescape(" ".join(shown))


# --- demo safety ---------------------------------------------------------------------------------

def test_sign_in_page_prints_the_synthetic_accounts_openly(webapp):
    page = webapp.get("/login")
    assert page.status == 200 and BANNER in page.body and RESET_NOTICE in page.text
    for name in ("Demo Analyst 1", "Demo Analyst 2", "Demo Controller"):
        assert name in page.body
    assert "There is no password" in page.body
    assert 'type="password"' not in page.body


@pytest.mark.parametrize("template", ["/runs", "/runs/{run}", "/runs/{run}/queue", "/exceptions/{p2}",
                                      "/upload", "/jobs", "/no-such-page"])
def test_every_page_carries_the_demo_banner(webapp, analyst, template):
    s = webapp.seeded
    page = webapp.get(template.format(run=s.run, p2=s.exceptions["p2"]), cookie=analyst)
    assert BANNER in page.body
    assert RESET_NOTICE in page.text


# --- figures come from the database ---------------------------------------------------------------

def test_run_page_shows_exactly_the_figures_in_run_overview(webapp, analyst):
    with webapp.db() as conn:
        row = conn.execute(
            "SELECT matches, exceptions, matches_exact, matches_gross_net, matches_many_to_one, "
            "exceptions_open, result_sha256 FROM run_overview WHERE run_id = %s", (webapp.seeded.run,)).fetchone()
    page = webapp.get(f"/runs/{webapp.seeded.run}", cookie=analyst).text
    for value in row:
        assert str(value) in page
    assert row[:5] == (65, 7, 39, 3, 23)


def test_the_web_source_does_no_arithmetic_on_figures():
    """The UI never computes a figure (D-076): no numeric conversion or aggregation anywhere in the
    web source. Formatting works on the strings pg returns."""
    forbidden = re.compile(r"\bNumber\(|parseFloat\(|parseInt\(|\.reduce\(|Math\.|BigInt\(")
    def code_lines(path):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith(("//", "*", "/*")):     # comments may name what is forbidden
                yield number, line.split("//", 1)[0]

    offenders = [f"{p.relative_to(WEB_SRC)}:{i}" for p in WEB_SRC.rglob("*.ts*")
                 for i, line in code_lines(p) if forbidden.search(line)]
    assert offenders == []


def test_amounts_are_shown_from_minor_units_without_conversion(webapp, analyst):
    page = webapp.get(f"/exceptions/{webapp.seeded.exceptions['p2']}", cookie=analyst).text
    assert "316.85" in page and "311.76" in page


# --- failure is visible, and distinguished ---------------------------------------------------------

def test_a_failed_run_is_listed_as_failed_with_its_reason(webapp, analyst):
    page = webapp.get("/runs", cookie=analyst).text
    assert f"#{webapp.seeded.failed_run}" in page
    assert "synthetic failure for the runs list" in page


def test_replay_outcomes_are_four_distinct_events_on_the_run_page(webapp, analyst):
    with webapp.db() as conn:
        original, drifted = (conn.execute(
            "SELECT encode(result_sha256, 'hex') FROM reconciliation_run WHERE id = %s", (rid,)).fetchone()[0]
            for rid in (webapp.seeded.run, webapp.seeded.drifted_replay))
        outcomes = dict(conn.execute(
            "SELECT run_id, replay_outcome FROM run_overview WHERE replay_of_run_id = %s",
            (webapp.seeded.run,)).fetchall())
    assert outcomes == {webapp.seeded.identical_replay: "identical", webapp.seeded.drifted_replay: "different",
                        webapp.seeded.failed_replay: "failed"}
    page = webapp.get(f"/runs/{webapp.seeded.run}", cookie=analyst).text
    # Drifted: shown as drift, with BOTH hashes.
    assert "Drift: the same inputs and engine version produced a different result." in page
    assert original in page and drifted in page
    # Failed to run: a different message, with the replay's own error.
    assert "Replay failed to run:" in page and "synthetic failure while replaying" in page
    # Refused before any run existed: a third message, with the job's reason.
    assert f"Replay job #{webapp.seeded.refused_replay_job} was refused and produced no run" in page


def test_a_drifted_replay_page_is_a_normal_page_not_an_error(webapp, analyst):
    page = webapp.get(f"/runs/{webapp.seeded.drifted_replay}", cookie=analyst)
    assert page.status == 200 and "different" in page.body


# --- replay is a job, not a call ------------------------------------------------------------------

def test_replay_request_enqueues_a_job_and_returns_without_running_the_engine(webapp, analyst):
    run = webapp.seeded.run
    runs_before = webapp.count("SELECT count(*) FROM reconciliation_run")
    first = webapp.post(f"/api/runs/{run}/replay", cookie=analyst)
    second = webapp.post(f"/api/runs/{run}/replay", cookie=analyst)
    assert first.status == second.status == 303
    assert _flash(webapp, first) == "Replay queued: the worker runs at 7 minutes past every hour."
    assert _flash(webapp, second) == "A replay of this run is already pending; nothing changed."
    assert webapp.count("SELECT count(*) FROM job WHERE kind = 'replay' AND replay_of_run_id = %s "
                        "AND status = 'queued'", run) == 1
    assert webapp.count("SELECT count(*) FROM reconciliation_run") == runs_before   # no engine ran
    page = webapp.get(f"/runs/{run}", cookie=analyst).text
    assert "Replay pending (job #" in page and "Queued: the worker runs at 7 minutes past every hour." in page


def test_replay_of_a_failed_run_is_refused(webapp, analyst):
    response = webapp.post(f"/api/runs/{webapp.seeded.failed_run}/replay", cookie=analyst)
    assert _flash(webapp, response) == "Only a finished run has a result to replay."


# --- requeue: idempotent, attempts preserved, budget respected --------------------------------------

def test_requeue_twice_results_in_one_requeue_and_preserves_attempts(webapp, controller):
    job = webapp.seeded.failed_job
    jobs_before = webapp.count("SELECT count(*) FROM job")
    first = webapp.post(f"/api/jobs/{job}/requeue", cookie=controller)
    second = webapp.post(f"/api/jobs/{job}/requeue", cookie=controller)
    assert "requeued. Its attempt count is unchanged." in _flash(webapp, first)
    assert "already queued; nothing changed" in _flash(webapp, second)
    assert webapp.count("SELECT count(*) FROM job") == jobs_before
    assert webapp.count("SELECT count(*) FROM job_requeue WHERE job_id = %s", job) == 1
    with webapp.db() as conn:
        assert conn.execute("SELECT status, attempts FROM job WHERE id = %s", (job,)).fetchone() == ("queued", 1)


def test_requeue_of_an_exhausted_job_is_refused_and_attempts_stay(webapp, controller):
    job = webapp.seeded.exhausted_job
    response = webapp.post(f"/api/jobs/{job}/requeue", cookie=controller)
    assert "used its whole attempt budget" in _flash(webapp, response)
    with webapp.db() as conn:
        status, attempts, budget = conn.execute(
            "SELECT status, attempts, recon_max_job_attempts() FROM job WHERE id = %s", (job,)).fetchone()
    assert (status, attempts) == ("failed", budget)


def test_requeue_button_is_shown_to_the_controller_only(webapp, analyst, controller):
    assert "/requeue" in webapp.get("/jobs", cookie=controller).body
    assert "/requeue" not in webapp.get("/jobs", cookie=analyst).body


# --- resolutions through the UI ----------------------------------------------------------------------

def test_resolve_then_correct_keeps_both_and_refusals_are_plain(webapp, analyst, controller):
    target = webapp.seeded.exceptions["p2"]
    path = f"/api/exceptions/{target}"
    short = webapp.post(f"{path}/resolve", cookie=analyst, form={"reason_code": "other_see_note", "note": "short"})
    assert "at least 10 characters" in _flash(webapp, short)
    assert webapp.count("SELECT count(*) FROM resolution WHERE exception_id = %s", target) == 0

    first = webapp.post(f"{path}/resolve", cookie=analyst, form={
        "reason_code": "processor_error_claim_raised", "note": "Synthetic: claim raised for EUR 0.40."})
    assert "recorded" in _flash(webapp, first)
    twice = webapp.post(f"{path}/resolve", cookie=controller, form={
        "reason_code": "other_see_note", "note": "Synthetic: a second first resolution."})
    assert "already resolved" in _flash(webapp, twice)

    with webapp.db() as conn:
        (first_id,) = conn.execute("SELECT id FROM resolution WHERE exception_id = %s", (target,)).fetchone()
    corrected = webapp.post(f"{path}/correct", cookie=controller, form={
        "reason_code": "funds_identified_and_posted", "note": "Synthetic: Orrery refunded on 6 Oct.",
        "supersedes_id": str(first_id)})
    assert "Correction recorded" in _flash(webapp, corrected)
    stale = webapp.post(f"{path}/correct", cookie=analyst, form={
        "reason_code": "other_see_note", "note": "Synthetic: correcting an old one.",
        "supersedes_id": str(first_id)})
    assert "already been corrected by someone else" in _flash(webapp, stale)

    page = webapp.get(f"/exceptions/{target}", cookie=analyst).text
    assert "superseded" in page and "in force" in page
    assert "Synthetic: claim raised for EUR 0.40." in page and "Synthetic: Orrery refunded on 6 Oct." in page


# --- upload and reconcile requests ------------------------------------------------------------------

def test_upload_stores_bytes_exactly_once_and_queues_one_parse(webapp, analyst):
    content = b"# SYNTHETIC DEMO DATA upload test\r\nentry_id,booked_on\r\n"
    first = webapp.upload(analyst, "ledger", "synthetic-upload.csv", content)
    assert "Queued for parsing: the worker runs at 7 minutes past every hour." in _flash(webapp, first)
    second = webapp.upload(analyst, "ledger", "renamed.csv", content)
    assert "already imported as file #" in _flash(webapp, second)
    with webapp.db() as conn:
        rows = conn.execute("SELECT f.raw, (SELECT count(*) FROM job j WHERE j.import_file_id = f.id) "
                            "FROM import_file f WHERE f.sha256 = sha256(%s::bytea)", (content,)).fetchall()
    assert len(rows) == 1 and bytes(rows[0][0]) == content and rows[0][1] == 1


def test_reconcile_request_is_queued_once(webapp, analyst):
    with webapp.db() as conn:
        files = conn.execute("SELECT ledger_file_id, settlement_file_id, bank_file_id FROM reconciliation_run "
                             "WHERE id = %s", (webapp.seeded.run,)).fetchone()
    form = dict(zip(("ledger_file_id", "settlement_file_id", "bank_file_id"), map(str, files)))
    first = webapp.post("/api/runs", cookie=analyst, form=form)
    second = webapp.post("/api/runs", cookie=analyst, form=form)
    assert "Reconciliation queued" in _flash(webapp, first)
    assert "already queued" in _flash(webapp, second)
    assert webapp.count("SELECT count(*) FROM job WHERE kind = 'reconcile' AND status = 'queued' "
                        "AND ledger_file_id = %s AND settlement_file_id = %s AND bank_file_id = %s", *files) == 1


def test_reconcile_request_refuses_a_file_in_the_wrong_slot(webapp, analyst):
    with webapp.db() as conn:
        bank, = conn.execute("SELECT bank_file_id FROM reconciliation_run WHERE id = %s",
                             (webapp.seeded.run,)).fetchone()
    response = webapp.post("/api/runs", cookie=analyst, form={
        "ledger_file_id": str(bank), "settlement_file_id": str(bank), "bank_file_id": str(bank)})
    assert "successfully parsed file of that kind" in _flash(webapp, response)


# --- plain queue status (D-088) -------------------------------------------------------------------

QUEUED_NOTE = "Queued: the worker runs at 7 minutes past every hour."
OVERDUE_NOTE = "Queued for over 70 minutes: the worker should have run by now."


def test_a_queued_replay_says_when_it_will_run_and_when_it_is_overdue(webapp, analyst):
    run = webapp.seeded.identical_replay          # a finished run no other test replays
    with webapp.db() as conn:
        (job_id,) = conn.execute("SELECT enqueue_replay(%s, %s)",
                                 (run, webapp.seeded.staff["Demo Analyst 1"])).fetchone()
    page = webapp.get(f"/runs/{run}", cookie=analyst).text
    assert f"Replay pending (job #{job_id})." in page and QUEUED_NOTE in page and OVERDUE_NOTE not in page
    with webapp.db() as conn:
        conn.execute("UPDATE job SET queued_at = now() - interval '71 minutes' WHERE id = %s", (job_id,))
    page = webapp.get(f"/runs/{run}", cookie=analyst).text
    assert OVERDUE_NOTE in page and QUEUED_NOTE not in page


def test_a_queued_reconciliation_and_a_queued_parse_show_the_schedule_on_the_upload_page(webapp, analyst):
    with webapp.db() as conn:
        ledger, settlement = conn.execute(
            "SELECT ledger_file_id, settlement_file_id FROM reconciliation_run WHERE id = %s",
            (webapp.seeded.run,)).fetchone()
        (job_id,) = conn.execute(
            "SELECT enqueue_reconcile(%s, %s, %s, %s)",
            (ledger, settlement, ledger, webapp.seeded.staff["Demo Analyst 1"])).fetchone()
    webapp.upload(analyst, "bank", "synthetic-queued.xml", b"<!-- SYNTHETIC DEMO DATA queued parse -->")
    page = webapp.get("/upload", cookie=analyst).text
    assert "Requested reconciliations" in page and f"#{job_id}" in page
    assert page.count(QUEUED_NOTE) >= 2            # the reconciliation and the file awaiting parsing


def test_the_jobs_page_shows_the_queue_status(webapp, analyst):
    assert QUEUED_NOTE in webapp.get("/jobs", cookie=analyst).text
