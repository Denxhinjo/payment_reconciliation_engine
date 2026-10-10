"""Requesters in the web UI (D-091): the signed-in user is recorded as the requester of what they
enqueue, the worker does not change it, and the system actor can never sign in and is labelled
wherever a name appears. Real HTTP against the production build."""

from __future__ import annotations

import re

import pytest

from recon import runs

SYSTEM_ACTOR = "Deployment seed"
SYSTEM_LABEL = re.compile(re.escape(SYSTEM_ACTOR) + r'\s*(?:<!-- -->)?\s*<span class="chip neutral">system actor</span>')


@pytest.fixture(scope="module")
def analyst(webapp):
    return webapp.sign_in("Demo Analyst 1")


def _system_id(webapp) -> str:
    return str(webapp.seeded.staff[SYSTEM_ACTOR])


# --- the system actor cannot sign in -------------------------------------------------------------

def test_the_system_actor_is_not_offered_on_the_sign_in_page(webapp):
    page = webapp.get("/login")
    assert page.status == 200 and "Demo Analyst 1" in page.body and SYSTEM_ACTOR not in page.body


def test_posting_the_system_actor_s_id_to_the_sign_in_endpoint_is_refused(webapp):
    response = webapp.post("/api/session", form={"staff_id": _system_id(webapp)})
    assert response.status == 303 and "/login" in response.location and "error=signin_unknown" in response.location
    assert "set-cookie" not in response.headers


def test_a_validly_signed_cookie_for_the_system_actor_is_no_session(webapp):
    """Even with the server's own secret, a session naming the system actor reads as anonymous."""
    cookie = webapp.forge_cookie(_system_id(webapp))
    page = webapp.get("/runs", cookie=cookie)
    assert page.status in (303, 307) and "/login" in page.location
    replay = webapp.post(f"/api/runs/{webapp.seeded.run}/replay", cookie=cookie)
    assert replay.status == 401


# --- the signed-in user is the requester; the worker keeps it ------------------------------------

def test_a_web_upload_records_the_uploader_as_the_parse_job_s_requester(webapp, analyst):
    content = b"# SYNTHETIC DEMO DATA requester upload\n"
    assert webapp.upload(analyst, "ledger", "synthetic-requester.csv", content).status == 303
    with webapp.db() as conn:
        (requested_by,) = conn.execute(
            "SELECT j.requested_by FROM job j JOIN import_file f ON f.id = j.import_file_id "
            "WHERE f.sha256 = sha256(%s::bytea)", (content,)).fetchone()
    assert requested_by == webapp.seeded.staff["Demo Analyst 1"]


def test_a_web_replay_records_the_signed_in_user_and_the_worker_does_not_change_it(webapp):
    cookie = webapp.sign_in("Demo Analyst 2")
    run = webapp.seeded.drifted_replay            # a finished run no other test replays
    assert webapp.post(f"/api/runs/{run}/replay", cookie=cookie).status == 303
    with webapp.db() as conn:
        (job_id, requested_by) = conn.execute(
            "SELECT id, requested_by FROM job WHERE kind = 'replay' AND replay_of_run_id = %s "
            "AND status = 'queued'", (run,)).fetchone()
        assert requested_by == webapp.seeded.staff["Demo Analyst 2"]
        processed, _ = runs.process_replay_job(conn, job_id)
        assert processed == job_id
        assert conn.execute("SELECT status, attempts, requested_by FROM job WHERE id = %s",
                            (job_id,)).fetchone() == ("done", 1, webapp.seeded.staff["Demo Analyst 2"])


# --- the system actor is labelled wherever names appear ------------------------------------------

def test_the_jobs_list_names_the_requester_and_labels_the_system_actor(webapp, analyst):
    page = webapp.get("/jobs", cookie=analyst).body
    assert "Requested by" in page and SYSTEM_LABEL.search(page) and "Demo Analyst 1" in page


def test_the_file_list_labels_a_system_upload(webapp, analyst):
    page = webapp.get("/upload", cookie=analyst).body
    row = page[re.search(rf"#(?:<!-- -->)?{webapp.seeded.system_file}<", page).start():]
    assert SYSTEM_LABEL.search(row[:row.index("</tr>")])


def test_the_run_history_labels_a_run_the_system_actor_requested(webapp, analyst):
    page = webapp.get("/runs", cookie=analyst).body
    assert "Requested by" in page
    row = page[page.index(f'href="/runs/{webapp.seeded.system_replay}"'):]
    assert SYSTEM_LABEL.search(row[:row.index("</tr>")])
