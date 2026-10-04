"""Server-side authorisation of the web UI (D-077): every route and every mutation, requested
directly, by every role that should not have it. A refusal is asserted on the actual response,
and a refused response must not carry the resource's data.

The KYC demo's one real defect was a page whose RESPONSE BODY carried data the UI never showed.
So every refusal here is also checked for leaked content, including the React Server Component
payload and prefetch variants of each page, which a browser never renders but an attacker can
request.
"""

from __future__ import annotations

import pytest

PAGE_TEMPLATES = ["/runs", "/runs/{run}", "/runs/{run}/queue", "/exceptions/{p2}", "/upload", "/jobs"]
MUTATION_TEMPLATES = [
    "/api/upload", "/api/runs", "/api/runs/{run}/replay", "/api/exceptions/{p2}/resolve",
    "/api/exceptions/{p2}/correct", "/api/jobs/{failed_job}/requeue",
]
# What a leaking response would contain: the run's result hash, P2's explanation and amounts,
# evidence identifiers, the failed job's error.
SECRETS_TEMPLATES = ["{run_hash}", "unexplained EUR 0.40", "316.85", "LE-0", "BT-0", "SYNBANK-",
                     "synthetic: worker crashed", "Pass exact"]


def _fill(webapp, template: str) -> str:
    s = webapp.seeded
    run_hash = webapp.count("SELECT encode(result_sha256, 'hex') FROM reconciliation_run WHERE id = %s", s.run) \
        if "{run_hash}" in template else ""
    return template.format(run=s.run, p2=s.exceptions["p2"], failed_job=s.failed_job, run_hash=run_hash)


def _assert_no_data(webapp, body: str) -> None:
    for template in SECRETS_TEMPLATES:
        secret = _fill(webapp, template)
        assert secret not in body, f"refused response leaks {secret!r}"


def _evidence_counts(webapp) -> tuple:
    return tuple(webapp.count(f"SELECT count(*) FROM {t}")
                 for t in ("import_file", "job", "resolution", "job_requeue"))


# --- anonymous: every page and every mutation ---------------------------------------------------

@pytest.mark.parametrize("template", PAGE_TEMPLATES)
@pytest.mark.parametrize("variant", ["html", "rsc", "prefetch"])
def test_anonymous_page_request_is_redirected_and_carries_no_data(webapp, template, variant):
    headers = {"html": {}, "rsc": {"RSC": "1"}, "prefetch": {"RSC": "1", "Next-Router-Prefetch": "1"}}[variant]
    response = webapp.get(_fill(webapp, template), headers=headers)
    if variant == "prefetch":
        # Next.js does not execute a dynamic page for a prefetch: the answer is only the route
        # tree (segment names and the id already in the URL) with an EMPTY page segment. Assert
        # exactly that, so a change that starts rendering the page here would fail this test.
        assert response.status == 200 and '"__PAGE__",{}' in response.body, response.body[:300]
        assert len(response.body) < 400, response.body[:400]
    else:
        redirected_status = (response.status in (303, 307, 308) and response.location
                             and "/login" in response.location)
        # An RSC request may answer 200 with a redirect instruction instead of a 3xx; either way
        # it must point at /login.
        redirected_payload = response.status == 200 and "/login" in response.body and variant == "rsc"
        assert redirected_status or redirected_payload, (response.status, response.body[:300])
    _assert_no_data(webapp, response.body)


@pytest.mark.parametrize("template", MUTATION_TEMPLATES)
def test_anonymous_mutation_is_refused_with_401_and_changes_nothing(webapp, template):
    before = _evidence_counts(webapp)
    response = webapp.post(_fill(webapp, template),
                           form={"reason_code": "other_see_note", "note": "Synthetic: anonymous attempt.",
                                 "supersedes_id": "1", "kind": "ledger", "ledger_file_id": "1",
                                 "settlement_file_id": "2", "bank_file_id": "3"})
    assert response.status == 401 and response.body == "Sign in required."
    assert _evidence_counts(webapp) == before


@pytest.mark.parametrize("cookie_kind", ["bad-signature", "expired", "unknown-staff", "garbage"])
def test_invalid_sessions_are_anonymous(webapp, cookie_kind):
    staff_id = str(webapp.seeded.staff["Demo Controller"])
    cookie = {
        "bad-signature": webapp.forge_cookie(staff_id, secret="not-the-server-secret-xxxxxxxxxxxxxx"),
        "expired": webapp.forge_cookie(staff_id, expires_in_ms=-1000),
        "unknown-staff": webapp.forge_cookie("987654321"),
        "garbage": "recon_session=not.a.session",
    }[cookie_kind]
    page = webapp.get(f"/runs/{webapp.seeded.run}", cookie=cookie)
    assert page.status in (303, 307) and "/login" in page.location
    mutation = webapp.post(f"/api/jobs/{webapp.seeded.failed_job}/requeue", cookie=cookie)
    assert mutation.status == 401


# --- analyst: what only a controller may do -----------------------------------------------------

@pytest.mark.parametrize("analyst", ["Demo Analyst 1", "Demo Analyst 2"])
def test_analyst_requeue_is_refused_with_403_and_changes_nothing(webapp, analyst):
    cookie = webapp.sign_in(analyst)
    jobs = (webapp.seeded.failed_job, webapp.seeded.exhausted_job)

    def job_state():
        with webapp.db() as conn:
            return conn.execute("SELECT id, status, attempts, error FROM job WHERE id = ANY(%s) ORDER BY id",
                                (list(jobs),)).fetchall()

    before, states_before = _evidence_counts(webapp), job_state()
    for job in jobs:
        response = webapp.post(f"/api/jobs/{job}/requeue", cookie=cookie)
        assert response.status == 403 and response.body == "This action requires the controller role."
        _assert_no_data(webapp, response.body)
    # Unchanged by the analyst's attempt (other tests may legitimately requeue as controller).
    assert _evidence_counts(webapp) == before and job_state() == states_before


def test_analyst_cannot_requeue_a_job_that_does_not_exist_either(webapp):
    """403, not 404: an analyst cannot even probe which job ids exist."""
    response = webapp.post("/api/jobs/987654321/requeue", cookie=webapp.sign_in("Demo Analyst 1"))
    assert response.status == 403


# --- signed-in staff: cross-site requests -------------------------------------------------------

@pytest.mark.parametrize("template", MUTATION_TEMPLATES)
def test_cross_site_post_is_refused_even_with_a_valid_session(webapp, template):
    cookie = webapp.sign_in("Demo Controller")
    before = _evidence_counts(webapp)
    response = webapp.post(_fill(webapp, template), cookie=cookie,
                           form={"reason_code": "other_see_note", "note": "Synthetic: cross-site attempt."},
                           headers={"Origin": "https://attacker.example"})
    assert response.status == 403 and response.body == "Cross-site request refused."
    assert _evidence_counts(webapp) == before


# --- method and identity hygiene ----------------------------------------------------------------

@pytest.mark.parametrize("template", MUTATION_TEMPLATES)
def test_mutations_do_not_answer_get(webapp, template):
    response = webapp.get(_fill(webapp, template), cookie=webapp.sign_in("Demo Controller"))
    assert response.status == 405


def test_sign_in_accepts_only_synthetic_staff(webapp):
    for value in ("987654321", "0", "abc", ""):
        response = webapp.post("/api/session", form={"staff_id": value})
        assert response.status == 303 and "/login" in response.location
        assert "set-cookie" not in response.headers


def test_resolved_by_is_the_signed_in_user_not_a_form_field(webapp):
    """A client cannot resolve in someone else's name by adding resolved_by to the form."""
    cookie = webapp.sign_in("Demo Analyst 2")
    target = webapp.seeded.exceptions["p5"]
    webapp.post(f"/api/exceptions/{target}/resolve", cookie=cookie, form={
        "reason_code": "funds_identified_and_posted", "note": "Synthetic: identified the sender.",
        "resolved_by": str(webapp.seeded.staff["Demo Controller"])})
    with webapp.db() as conn:
        (resolved_by,) = conn.execute(
            "SELECT s.display_name FROM resolution r JOIN staff_user s ON s.id = r.resolved_by "
            "WHERE r.exception_id = %s", (target,)).fetchone()
    assert resolved_by == "Demo Analyst 2"
