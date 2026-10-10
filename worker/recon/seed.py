"""Seed the public demo's reference database through the real job path (D-092).

``seed_demo`` fills an empty, migrated database with what the demo opens on: the synthetic demo
month, run #1 and its replay (run #2). Nothing is inserted by hand. Every step is the code the
web app and the worker use:

1. the three files are uploaded with ``importer.store_file`` (the database function
   ``upload_file()``), as the system actor "Deployment seed";
2. their parse jobs are processed by ``importer.process_parse_job`` (claim, attempt, lease, done);
3. the reconcile is queued by ``jobs.enqueue_reconcile`` (the function the web's "Reconcile"
   form calls) and processed by ``runs.process_reconcile_job``;
4. run #1's result hash must equal the golden hash for this engine version, exactly;
5. the replay is queued by ``jobs.enqueue_replay`` and processed by ``runs.process_replay_job``;
   it must be identical.

Fail closed: the whole seed is one database transaction. Any failure, including a hash that
differs from the golden value, rolls everything back and leaves the database empty of files,
jobs and runs. There is no partially seeded state to mistake for a ready one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import psycopg

from recon import importer, jobs, runs
from recon.engine import ENGINE_VERSION

SYSTEM_ACTOR = "Deployment seed"

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_DATA_DIR = REPO_ROOT / "demo-data" / "2026-09"
GOLDEN_FILE = REPO_ROOT / "worker" / "tests" / "golden" / "results.json"
GOLDEN_KEY = "demo-data/2026-09 (seed 20260901)"

DEMO_FILES = (
    ("ledger", "synthetic_ledger_2026-09.csv"),
    ("settlement", "synthetic_orrery_settlement_2026-09.csv"),
    ("bank", "synthetic_bank_camt053_2026-09.xml"),
)


class SeedFailed(Exception):
    """The seed did not produce the expected demo; nothing was kept."""


@dataclass(frozen=True)
class SeedReport:
    file_ids: tuple[int, int, int]
    run_id: int
    run_sha256: str
    matches: int
    exceptions: int
    replay_run_id: int
    replay_sha256: str
    job_ids: tuple[int, ...]


def golden_sha256(golden_file: Path = GOLDEN_FILE) -> str:
    """The pinned result hash of the demo month for this engine version. Never edited to pass."""
    entries = json.loads(golden_file.read_text(encoding="utf-8"))
    try:
        return entries[ENGINE_VERSION][GOLDEN_KEY]
    except KeyError:
        raise SeedFailed(f"no golden hash for engine {ENGINE_VERSION} in {golden_file}") from None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SeedFailed(message)


def seed_demo(conn: psycopg.Connection, *, expected_sha256: str, data_dir: Path = DEMO_DATA_DIR,
              engine_git_sha: str | None = None) -> SeedReport:
    """Seed the demo in one transaction; raise SeedFailed (or the underlying error) on any problem."""
    with conn.transaction():
        for table in ("import_file", "job", "reconciliation_run"):
            (count,) = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            _require(count == 0, f"refusing to seed: {table} already has {count} row(s); "
                                 "seed only an empty, migrated database")

        importer.seed_synthetic_staff(conn)
        row = conn.execute("SELECT id, role FROM staff_user WHERE display_name = %s",
                           (SYSTEM_ACTOR,)).fetchone()
        _require(row is not None and row[1] == "system",
                 f"no system actor {SYSTEM_ACTOR!r}; is migration 0012 applied?")
        actor = row[0]
        job_ids: list[int] = []

        # 1-2. Upload as the system actor, then parse through the worker's job path.
        file_ids = []
        for kind, name in DEMO_FILES:
            stored = importer.store_file(conn, kind, name, (data_dir / name).read_bytes(), actor)
            _require(stored.created and stored.job_id is not None, f"{name} was not newly stored")
            parsed = importer.process_parse_job(conn, stored.job_id)
            _require(parsed is not None and parsed.status == "parsed",
                     f"{name} did not parse: {parsed}")
            file_ids.append(stored.file_id)
            job_ids.append(stored.job_id)

        # 3-4. Reconcile, queued the way the web queues it, run by the worker's job path.
        job_id = jobs.enqueue_reconcile(conn, *file_ids, actor)
        _require(job_id is not None, "the reconcile job was not queued")
        processed = runs.process_reconcile_job(conn, job_id, engine_git_sha=engine_git_sha)
        _require(processed is not None and processed[0] == job_id, "the reconcile job was not processed")
        run = processed[1]
        _require(run.status == "finished", f"run #{run.run_id} did not finish: {run.error}")
        _require(run.result_sha256 == expected_sha256,
                 f"run #{run.run_id} result sha256 {run.result_sha256} differs from the golden "
                 f"{expected_sha256}; the engine's output changed")
        job_ids.append(job_id)

        # 5. Replay, queued and run the same way.
        job_id = jobs.enqueue_replay(conn, run.run_id, actor)
        _require(job_id is not None, "the replay job was not queued")
        processed = runs.process_replay_job(conn, job_id, engine_git_sha=engine_git_sha)
        _require(processed is not None and processed[0] == job_id, "the replay job was not processed")
        replay = processed[1]
        _require(not isinstance(replay, str), f"the replay was refused: {replay}")
        _require(replay.identical, f"replay run #{replay.replay_run_id} is not identical: "
                                   f"{replay.replay_sha256} vs {replay.original_sha256}")
        job_ids.append(job_id)

        # Every job went through once, normally, and names the system actor.
        for job_id, status, attempts, requested_by in conn.execute(
                "SELECT id, status, attempts, requested_by FROM job ORDER BY id"):
            _require(status == "done" and attempts == 1 and requested_by == actor,
                     f"job #{job_id}: status {status}, attempts {attempts}, requester {requested_by}")

        return SeedReport(tuple(file_ids), run.run_id, run.result_sha256, run.matches,
                          run.exceptions, replay.replay_run_id, replay.replay_sha256, tuple(job_ids))
