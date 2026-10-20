"""The redesigned screens (D-096) show exactly the figures the database views hold, and make no
claim the system does not back. Real HTTP against the production build."""

from __future__ import annotations

import html
import re

import pytest


@pytest.fixture(scope="module")
def analyst(webapp):
    return webapp.sign_in("Demo Analyst 1")


def _text(page: str) -> str:
    """Visible text, roughly: markup and React's separators removed, entities decoded."""
    page = re.sub(r"<script\b.*?</script>", "", page, flags=re.S).replace("<!-- -->", "")
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)))


def _minor(value) -> str:
    digits = f"{int(value):03d}"
    whole = f"{int(digits[:-2]):,}"
    return f"{whole}.{digits[-2:]}"


def test_the_dashboard_shows_the_latest_run_figures(webapp, analyst):
    with webapp.db() as conn:
        run = conn.execute("SELECT run_id FROM latest_run").fetchone()[0]
        f = conn.execute("SELECT period_label, rows_compared, ledger_rows, settlement_rows, bank_entries, "
                         "open_needing_review, open_timing FROM run_figures WHERE run_id = %s", (run,)).fetchone()
        o = conn.execute("SELECT matches, exceptions, exceptions_open, matches_exact FROM run_overview WHERE run_id = %s",
                         (run,)).fetchone()
    assert run == webapp.seeded.run
    page = _text(webapp.get("/runs", cookie=analyst).text)
    assert f[0] in page and f"{f[1]:,}" in page
    assert f"{f[2]} ledger · {f[3]} settlement · {f[4]} bank" in page
    assert f"{f[5]} need review" in page and f"{f[6]} timing" in page
    assert f"{o[2]} of {o[1]} raised" in page and f"{o[3]} exact" in page


def test_the_dashboard_shows_planted_problems_caught_by_the_database(webapp, analyst):
    with webapp.db() as conn:
        caught, total = conn.execute(
            "SELECT count(*) FILTER (WHERE caught), count(*) FROM run_planted_items "
            "WHERE run_id = %s AND category = 'planted'", (webapp.seeded.run,)).fetchone()
    assert (caught, total) == (5, 5)
    page = _text(webapp.get("/runs", cookie=analyst).text)
    assert "5 of 5 caught" in page and "Missing payout" in page and "Expected timing differences" in page


def test_the_queue_shows_the_value_under_review_and_counts_from_the_database(webapp, analyst):
    with webapp.db() as conn:
        value, review, timing, resolved = conn.execute(
            "SELECT open_value_under_review_minor, open_needing_review, open_timing, resolved FROM run_figures "
            "WHERE run_id = %s", (webapp.seeded.run,)).fetchone()
    for path in ("/exceptions", f"/runs/{webapp.seeded.run}/queue"):
        page = _text(webapp.get(path, cookie=analyst).text)
        assert f"EUR {_minor(value)}" in page
        assert f"Needs review {review}" in page and f"Timing {timing}" in page and f"Resolved {resolved}" in page


def test_the_queue_lists_breaks_needing_review_before_timing_items(webapp, analyst):
    page = webapp.get(f"/runs/{webapp.seeded.run}/queue?status=all", cookie=analyst).text
    reasons = re.findall(r'<span class="chip reason (?:warn|slate)">(\w+)</span>', page)
    assert reasons and "timing" in reasons
    first_timing = reasons.index("timing")
    assert all(r == "timing" for r in reasons[first_timing:])


def test_the_mismatch_page_shows_the_database_arithmetic_and_no_unbacked_fee_claim(webapp, analyst):
    page = _text(webapp.get(f"/exceptions/{webapp.seeded.exceptions['p2']}", cookie=analyst).text)
    assert "Expected at bank 312.16" in page and "Received 311.76" in page and "Shortfall EUR 0.40" in page
    assert "does not check it against a fee schedule" in page
    assert "fee is correct" not in page


def test_the_jobs_page_counts_come_from_the_database(webapp, analyst):
    with webapp.db() as conn:
        total, queued, running, failed, done = conn.execute("SELECT * FROM job_counts").fetchone()
    page = _text(webapp.get("/jobs", cookie=analyst).text)
    assert f"Total {total} Queued {queued} Running {running} Failed {failed} Done {done}" in page


def test_the_sign_in_page_shows_counts_but_no_evidence(webapp):
    """Public counts of the demo month only (D-099): no row, amount, explanation or hash."""
    with webapp.db() as conn:
        ledger_rows = conn.execute("SELECT ledger_rows FROM run_figures WHERE run_id = %s",
                                   (webapp.seeded.run,)).fetchone()[0]
        run_hash = conn.execute("SELECT result_sha256 FROM run_overview WHERE run_id = %s",
                                (webapp.seeded.run,)).fetchone()[0]
    body = webapp.get("/login").body
    assert f"{ledger_rows} rows" in _text(body)
    for secret in (run_hash, run_hash[:8], "unexplained EUR 0.40", "316.85", "LE-0", "BT-0", "SYNBANK-", "Pass exact"):
        assert secret not in body, secret


def test_the_exceptions_page_needs_a_session(webapp):
    response = webapp.get("/exceptions")
    assert response.status in (303, 307) and "/login" in response.location


def test_the_upload_page_says_where_to_get_synthetic_test_files(webapp, analyst):
    """The ready-to-upload links name the committed demo files, and the page warns that the demo is public."""
    from pathlib import Path
    body = webapp.get("/upload", cookie=analyst).text
    demo = Path(__file__).resolve().parents[2] / "demo-data" / "2026-09"
    linked = re.findall(r'href="https://raw\.githubusercontent\.com/Denxhinjo/payment_reconciliation_engine/main/demo-data/2026-09/([^"]+)"', body)
    assert sorted(linked) == sorted(p.name for p in demo.iterdir() if p.suffix in (".csv", ".xml"))
    text = _text(body)
    assert "Need test files?" in text and "must first be converted to these three formats" in text
    assert "Upload synthetic data only" in text
