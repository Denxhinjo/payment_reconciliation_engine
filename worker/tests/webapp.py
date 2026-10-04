"""Harness for end-to-end tests of the web UI (stage 7, D-079).

Starts the real production build of web/ (`next start`) against a fresh database, connected as a
login role that is a member of recon_web, so the database's own privileges apply exactly as in
deployment. Tests then make real HTTP requests: a refusal is asserted on an actual response, not
on a missing button.

The build is reused when it is newer than every file under web/src and web's config, and rebuilt
otherwise, so the tests never run against stale code. Node is required: without it these tests
FAIL (D-036), they do not skip.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import http.client
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

import psycopg
import pytest

from recon import engine, importer, runs
from recon.migrate import migrate

REPO = Path(__file__).resolve().parents[2]
WEB = REPO / "web"
DEMO = REPO / "demo-data" / "2026-09"
DEMO_FILES = {
    "ledger": DEMO / "synthetic_ledger_2026-09.csv",
    "settlement": DEMO / "synthetic_orrery_settlement_2026-09.csv",
    "bank": DEMO / "synthetic_bank_camt053_2026-09.xml",
}
WEB_LOGIN = "recon_web_e2e"
WEB_PASSWORD = "synthetic-e2e-only"          # a test-only role on a disposable server
SESSION_SECRET = "synthetic-e2e-session-secret-not-a-real-secret"


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.fail("Node.js is required for the web end-to-end tests (see README).", pytrace=False)
    return node


def ensure_build() -> None:
    build_id = WEB / ".next" / "BUILD_ID"
    sources = [p for p in (WEB / "src").rglob("*") if p.is_file()]
    sources += [WEB / name for name in ("package.json", "next.config.ts", "tsconfig.json")]
    newest_source = max(p.stat().st_mtime for p in sources)
    if build_id.exists() and build_id.stat().st_mtime > newest_source:
        return
    npm = shutil.which("npm") or pytest.fail("npm is required to build the web app.", pytrace=False)
    if not (WEB / "node_modules").exists():
        subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=WEB, check=True)
    subprocess.run([npm, "run", "build"], cwd=WEB, check=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: str

    @property
    def location(self) -> str | None:
        return self.headers.get("location")

    @property
    def text(self) -> str:
        """The body with React's text-boundary markers removed: `#{id}` renders as `#<!-- -->5`."""
        return self.body.replace("<!-- -->", "")


@dataclass
class Seeded:
    run: int                     # finished demo run
    failed_run: int              # a run that failed, with an error
    identical_replay: int
    drifted_replay: int
    failed_replay: int
    refused_replay_job: int      # a replay job refused before any run existed
    exceptions: dict[str, int]   # p1, p2, p4, p5 -> exception id
    failed_job: int              # failed, attempts 1: requeueable
    exhausted_job: int           # failed, attempts at the budget
    staff: dict[str, int]        # display name -> id


class WebApp:
    def __init__(self, port: int, database_url: str, seeded: Seeded):
        self.port = port
        self.database_url = database_url
        self.seeded = seeded

    # --- HTTP ---------------------------------------------------------------------------------

    def request(self, method: str, path: str, *, cookie: str | None = None, form: dict | None = None,
                body: bytes | None = None, headers: dict | None = None) -> Response:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        all_headers = dict(headers or {})
        if cookie:
            all_headers["Cookie"] = cookie
        if form is not None:
            body = urlencode(form).encode()
            all_headers["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body=body, headers=all_headers)
        raw = conn.getresponse()
        response = Response(raw.status, {k.lower(): v for k, v in raw.getheaders()},
                            raw.read().decode("utf-8", errors="replace"))
        conn.close()
        return response

    def get(self, path: str, **kwargs) -> Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> Response:
        return self.request("POST", path, **kwargs)

    def upload(self, cookie: str, kind: str, name: str, content: bytes, headers: dict | None = None) -> Response:
        boundary = uuid.uuid4().hex
        body = b"".join([
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\n{kind}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n".encode(), content, b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        return self.post("/api/upload", cookie=cookie, body=body,
                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}", **(headers or {})})

    # --- sessions -----------------------------------------------------------------------------

    def sign_in(self, display_name: str) -> str:
        response = self.post("/api/session", form={"staff_id": str(self.seeded.staff[display_name])})
        assert response.status == 303, response
        cookie = response.headers["set-cookie"].split(";", 1)[0]
        assert cookie.startswith("recon_session=")
        return cookie

    @staticmethod
    def forge_cookie(staff_id: str, *, expires_in_ms: int = 3_600_000, secret: str = SESSION_SECRET) -> str:
        """A session cookie signed like the app signs them (used to test expiry and bad ids)."""
        now = int(time.time() * 1000)
        payload = base64.urlsafe_b64encode(json.dumps(
            {"sid": staff_id, "iat": now, "exp": now + expires_in_ms}).encode()).rstrip(b"=").decode()
        signature = base64.urlsafe_b64encode(hmac.new(
            secret.encode(), payload.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
        return f"recon_session={payload}.{signature}"

    # --- database -----------------------------------------------------------------------------

    def db(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, autocommit=True)

    def count(self, sql: str, *params) -> int:
        with self.db() as conn:
            return conn.execute(sql, params).fetchone()[0]


def _seed(conn: psycopg.Connection) -> Seeded:
    importer.seed_synthetic_staff(conn)
    staff = dict(conn.execute("SELECT display_name, id FROM staff_user").fetchall())
    files = {}
    for kind, path in DEMO_FILES.items():
        stored = importer.store_file(conn, kind, path.name, path.read_bytes(), staff["Demo Analyst 1"])
        importer.process_parse_job(conn, stored.job_id)
        files[kind] = stored.file_id
    run = runs.create_and_run(conn, files["ledger"], files["settlement"], files["bank"]).run_id

    identical = runs.replay(conn, run).replay_run_id
    real = engine.run_engine

    def drifted(*raws):
        result = real(*raws)
        first = dataclasses.replace(result.exceptions[0],
                                    explanation=result.exceptions[0].explanation + " (drifted)")
        return dataclasses.replace(result, exceptions=(first, *result.exceptions[1:]))

    def broken(*raws):
        raise engine.EngineInputError("synthetic failure while replaying")

    with mock.patch.object(runs, "run_engine", drifted):
        drifted_replay = runs.replay(conn, run).replay_run_id
    with mock.patch.object(runs, "run_engine", broken):
        failed_replay = runs.replay(conn, run).replay_run_id

    (failed_run,) = conn.execute(
        "INSERT INTO reconciliation_run (engine_version, ledger_file_id, settlement_file_id, bank_file_id) "
        "VALUES ('1.0.0', %s, %s, %s) RETURNING id", (files["ledger"], files["settlement"], files["bank"])).fetchone()
    conn.execute("UPDATE reconciliation_run SET status = 'failed', finished_at = now(), "
                 "error = 'EngineInputError: synthetic failure for the runs list' WHERE id = %s", (failed_run,))

    (refused_job,) = conn.execute(
        "INSERT INTO job (kind, replay_of_run_id, status, attempts, error, finished_at) VALUES "
        "('replay', %s, 'failed', 1, 'run was computed by engine 0.9.0; check out git tag engine-v0.9.0', now()) "
        "RETURNING id", (run,)).fetchone()

    (budget,) = conn.execute("SELECT recon_max_job_attempts()").fetchone()
    jobs = []
    for attempts in (1, budget):
        stored = importer.store_file(conn, "ledger", f"synthetic-{attempts}.csv",
                                     f"# SYNTHETIC DEMO DATA job {attempts}\n".encode(), staff["Demo Analyst 1"])
        conn.execute("UPDATE job SET status = 'failed', attempts = %s, finished_at = now(), "
                     "error = %s WHERE id = %s",
                     (attempts, f"synthetic: worker crashed on attempt {attempts}", stored.job_id))
        jobs.append(stored.job_id)

    by_reason = {}
    for exception_id, reason in conn.execute(
            "SELECT id, suggested_reason FROM run_exception WHERE run_id = %s ORDER BY ordinal", (run,)):
        by_reason.setdefault(reason, exception_id)
    return Seeded(
        run=run, failed_run=failed_run, identical_replay=identical, drifted_replay=drifted_replay,
        failed_replay=failed_replay, refused_replay_job=refused_job,
        exceptions={"p1": by_reason["missing_from_bank"], "p2": by_reason["amount_mismatch"],
                    "p4": by_reason["possible_duplicate"], "p5": by_reason["unknown_deposit"]},
        failed_job=jobs[0], exhausted_job=jobs[1], staff=staff,
    )


def start_webapp(admin_url: str, database_url: str, dbname: str) -> tuple[WebApp, subprocess.Popen]:
    _node()
    ensure_build()
    migrate(database_url)
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{WEB_LOGIN}') THEN "
            f"CREATE ROLE {WEB_LOGIN} LOGIN PASSWORD '{WEB_PASSWORD}' IN ROLE recon_web; END IF; END $$")
        seeded = _seed(conn)
    host = psycopg.conninfo.conninfo_to_dict(admin_url).get("host", "127.0.0.1")
    port_db = psycopg.conninfo.conninfo_to_dict(admin_url).get("port", "5432")
    web_url = f"postgresql://{WEB_LOGIN}:{WEB_PASSWORD}@{host}:{port_db}/{dbname}"

    port = _free_port()
    env = dict(os.environ, DATABASE_URL=web_url, SESSION_SECRET=SESSION_SECRET, NODE_ENV="production",
               PORT=str(port))
    # Server output goes to a file, not a pipe: an unread pipe fills up and freezes the server.
    log_path = Path(tempfile.gettempdir()) / f"recon-web-e2e-{port}.log"
    log = open(log_path, "wb")
    process = subprocess.Popen(
        [_node(), str(WEB / "node_modules" / "next" / "dist" / "bin" / "next"), "start", "-p", str(port),
         "-H", "127.0.0.1"],
        cwd=WEB, env=env, stdout=log, stderr=subprocess.STDOUT)
    process.log_path = log_path   # type: ignore[attr-defined]
    app = WebApp(port, database_url, seeded)
    deadline = time.time() + 90
    while time.time() < deadline:
        if process.poll() is not None:
            pytest.fail(f"next start exited early:\n{log_path.read_text(errors='replace')}")
        try:
            if app.get("/login").status == 200:
                return app, process
        except OSError:
            pass
        time.sleep(0.5)
    process.kill()
    pytest.fail("the web app did not start within 90 seconds")
