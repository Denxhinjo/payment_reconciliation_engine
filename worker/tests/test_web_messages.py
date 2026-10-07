"""Flash messages are codes looked up server-side (D-082, closes F18). Arbitrary text in a URL is
never rendered: unknown codes, built-in property names and malformed references show nothing."""

from __future__ import annotations

import re

import pytest

FLASH = re.compile(r'<p class="flash (?:ok|bad)" role="(?:status|alert)">(.*?)</p>')

CRAFTED = [
    "Your account is locked. Call +1-555-0100 to restore access",
    "<script>alert(1)</script>",
    "<b>Payment approved</b>",
    "upload_stored extra words",
    "UPLOAD_STORED",
]


@pytest.fixture(scope="module")
def viewer(webapp):
    return webapp.sign_in("Demo Analyst 2")


def _shown(webapp, path, cookie=None) -> list[str]:
    return FLASH.findall(webapp.get(path, cookie=cookie).text)


def _page(webapp, path, cookie=None) -> str:
    return webapp.get(path, cookie=cookie).text


@pytest.mark.parametrize("payload", CRAFTED)
@pytest.mark.parametrize("param", ["notice", "error"])
@pytest.mark.parametrize("page, signed_in", [("/login", False), ("/runs", True), ("/jobs", True), ("/upload", True)])
def test_arbitrary_text_in_the_url_is_never_rendered(webapp, viewer, payload, param, page, signed_in):
    from urllib.parse import quote
    path = f"{page}?{param}={quote(payload)}&ref=12"
    body = _page(webapp, path, viewer if signed_in else None)
    assert FLASH.findall(body) == []
    # Next.js serialises the router state, including the URL's own query string, into <script>
    # payloads; that is data the browser never displays (the same text is in the address bar).
    # Everything a person can see, and every attribute, is outside <script>: the crafted text
    # must appear nowhere there.
    markup = re.sub(r"<script\b.*?</script>", "", body, flags=re.S)
    for fragment in ("account is locked", "555-0100", "alert(1)", "Payment approved", "extra words"):
        assert fragment not in markup


@pytest.mark.parametrize("code", ["no_such_code", "constructor", "__proto__", "toString",
                                  "hasOwnProperty", "valueOf", ""])
def test_unknown_codes_and_builtin_names_show_nothing(webapp, viewer, code):
    assert _shown(webapp, f"/runs?notice={code}&error={code}&ref=1", viewer) == []


@pytest.mark.parametrize("ref", ["", "0", "-1", "12a", "12%3Cb%3E", "1.5", "99999999999999999999"])
def test_a_message_needing_a_reference_shows_nothing_without_a_valid_one(webapp, viewer, ref):
    assert _shown(webapp, f"/upload?notice=upload_stored&ref={ref}", viewer) == []


def test_a_known_code_renders_its_fixed_text(webapp, viewer):
    assert _shown(webapp, "/upload?notice=upload_stored&ref=12", viewer) == [
        "Stored as file #12; queued for parsing."]
    assert _shown(webapp, "/jobs?error=job_attempts_exhausted&ref=7", viewer) == [
        "Job #7 has used its whole attempt budget and cannot be requeued. Requeue never resets "
        "attempts (poison-pill protection)."]


def test_a_reference_is_ignored_by_messages_that_take_none(webapp, viewer):
    assert _shown(webapp, "/runs?notice=replay_queued&ref=%3Cscript%3Ex%3C%2Fscript%3E", viewer) == [
        "Replay queued."]


def test_sign_in_errors_are_codes_too(webapp):
    response = webapp.post("/api/session", form={"staff_id": "987654321"})
    assert response.location.endswith("/login?error=signin_unknown")
    assert _shown(webapp, "/login?error=signin_unknown") == ["Unknown staff account."]


def test_every_redirect_from_a_mutation_carries_only_codes(webapp):
    """No route builds a message from free text: every notice/error value in the source is a key
    of the fixed tables (the TypeScript types enforce it; this checks the shipped source too)."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "web" / "src"
    messages = (src / "lib" / "messages.ts").read_text(encoding="utf-8")
    codes = set(re.findall(r"^\s+([a-z_]+): \"", messages, re.M))
    used = set()
    for path in (src / "app" / "api").rglob("*.ts"):
        used |= set(re.findall(r'(?:notice|error): "([^"]+)"', path.read_text(encoding="utf-8")))
        assert not re.search(r"(?:notice|error): `", path.read_text(encoding="utf-8")), path
    assert used and used <= codes, used - codes
