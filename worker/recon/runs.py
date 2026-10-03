"""Reconciliation runs in the database: record, compute, persist, finish.

1. A ``reconciliation_run`` row is inserted as 'running' in its own transaction, so even a run
   that fails is on record with its engine version and inputs.
2. In one transaction: the three raw files are loaded, the engine runs on them (D-007), and the
   result is written: matches, exceptions, and one allocation per input row, mapped from the
   engine's natural keys to row ids. Then the run is set to 'finished' with its canonical
   result. That transition fires the database finish check (D-012), which re-verifies every
   match's shape and arithmetic in SQL. If it disagrees with the engine, the run cannot finish.
3. Any engine input error, parser-version mismatch or database refusal leaves the run 'failed'
   with the reason. Nothing half-written survives.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import psycopg

from recon.engine import ENGINE_VERSION, EngineInputError, run_engine
from recon.parse import PARSER_VERSION


@dataclass(frozen=True)
class RunOutcome:
    run_id: int
    status: str                 # 'finished' or 'failed'
    error: str | None
    result_sha256: str | None
    matches: int
    exceptions: int


class RunRefused(Exception):
    """Raised inside the run transaction to fail the run with a specific reason."""


def _load_inputs(conn: psycopg.Connection, run_id: int) -> dict[str, bytes]:
    rows = conn.execute(
        "SELECT p.kind::text, f.raw, p.parser_version "
        "FROM reconciliation_run r "
        "JOIN import_parse p ON p.import_file_id IN (r.ledger_file_id, r.settlement_file_id, r.bank_file_id) "
        "JOIN import_file f ON f.id = p.import_file_id "
        "WHERE r.id = %s",
        (run_id,),
    ).fetchall()
    inputs = {}
    for kind, raw, parser_version in rows:
        if parser_version != PARSER_VERSION:
            raise RunRefused(
                f"the {kind} file was parsed by parser {parser_version} but this engine uses parser "
                f"{PARSER_VERSION}; its stored rows may differ from what the engine sees"
            )
        inputs[kind] = bytes(raw)
    return inputs


def _ids_by_key(conn: psycopg.Connection, run_id: int) -> dict[str, dict[int, int]]:
    ledger_file, settlement_file, bank_file = conn.execute(
        "SELECT ledger_file_id, settlement_file_id, bank_file_id FROM reconciliation_run WHERE id = %s",
        (run_id,),
    ).fetchone()
    return {
        "ledger": dict(conn.execute(
            "SELECT row_number, id FROM ledger_entry WHERE import_file_id = %s", (ledger_file,))),
        "settlement": dict(conn.execute(
            "SELECT row_number, id FROM settlement_line WHERE import_file_id = %s", (settlement_file,))),
        "bank": dict(conn.execute(
            "SELECT entry_index, id FROM bank_entry WHERE import_file_id = %s", (bank_file,))),
        "files": {"ledger": ledger_file, "settlement": settlement_file, "bank": bank_file},
    }


def create_and_run(conn: psycopg.Connection, ledger_file_id: int, settlement_file_id: int,
                   bank_file_id: int, *, replay_of_run_id: int | None = None,
                   engine_git_sha: str | None = None) -> RunOutcome:
    with conn.transaction():
        (run_id,) = conn.execute(
            "INSERT INTO reconciliation_run (engine_version, engine_git_sha, ledger_file_id, "
            "settlement_file_id, bank_file_id, replay_of_run_id) VALUES (%s, %s, %s, %s, %s, %s) "
            "RETURNING id",
            (ENGINE_VERSION, engine_git_sha, ledger_file_id, settlement_file_id, bank_file_id,
             replay_of_run_id),
        ).fetchone()

    try:
        with conn.transaction():
            inputs = _load_inputs(conn, run_id)
            result = run_engine(inputs["ledger"], inputs["settlement"], inputs["bank"])
            canonical = result.canonical()
            ids = _ids_by_key(conn, run_id)
            files = ids["files"]

            allocations: dict[str, list[tuple]] = {"ledger": [], "settlement": [], "bank": []}
            for match in result.matches:
                (match_id,) = conn.execute(
                    "INSERT INTO run_match (run_id, ordinal, pass, explanation) "
                    "VALUES (%s, %s, %s, %s) RETURNING id",
                    (run_id, match.ordinal, match.pass_name, match.explanation),
                ).fetchone()
                for kind in allocations:
                    allocations[kind].extend(
                        (run_id, files[kind], ids[kind][key], match_id, None)
                        for key in getattr(match, kind))
            for exception in result.exceptions:
                (exception_id,) = conn.execute(
                    "INSERT INTO run_exception (run_id, ordinal, suggested_reason, explanation, "
                    "fingerprint) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (run_id, exception.ordinal, exception.reason, exception.explanation,
                     bytes.fromhex(exception.fingerprint)),
                ).fetchone()
                for kind in allocations:
                    allocations[kind].extend(
                        (run_id, files[kind], ids[kind][key], None, exception_id)
                        for key in getattr(exception, kind))

            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO allocation_ledger (run_id, ledger_file_id, ledger_entry_id, "
                    "match_id, exception_id) VALUES (%s, %s, %s, %s, %s)", allocations["ledger"])
                cur.executemany(
                    "INSERT INTO allocation_settlement (run_id, settlement_file_id, "
                    "settlement_line_id, match_id, exception_id) VALUES (%s, %s, %s, %s, %s)",
                    allocations["settlement"])
                cur.executemany(
                    "INSERT INTO allocation_bank (run_id, bank_file_id, bank_entry_id, match_id, "
                    "exception_id) VALUES (%s, %s, %s, %s, %s)", allocations["bank"])

            # Fires the database finish check (D-012).
            conn.execute(
                "UPDATE reconciliation_run SET status = 'finished', finished_at = now(), "
                "result_canonical = %s WHERE id = %s",
                (canonical, run_id),
            )
        return RunOutcome(run_id, "finished", None, hashlib.sha256(canonical).hexdigest(),
                          len(result.matches), len(result.exceptions))
    except (EngineInputError, RunRefused, psycopg.Error) as exc:
        error = f"{type(exc).__name__}: {exc}"
        with conn.transaction():
            conn.execute(
                "UPDATE reconciliation_run SET status = 'failed', finished_at = now(), error = %s "
                "WHERE id = %s",
                (error, run_id),
            )
        return RunOutcome(run_id, "failed", error, None, 0, 0)


def process_reconcile_job(conn: psycopg.Connection, job_id: int | None = None,
                          engine_git_sha: str | None = None) -> tuple[int, RunOutcome] | None:
    """Claim one queued reconcile job and run it. A failed *run* is a recorded outcome; the job
    is 'done' either way and points at the run (D-052)."""
    with conn.transaction():
        row = conn.execute(
            "SELECT id, ledger_file_id, settlement_file_id, bank_file_id FROM job "
            "WHERE kind = 'reconcile' AND status = 'queued' AND (%s::bigint IS NULL OR id = %s) "
            "ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED",
            (job_id, job_id),
        ).fetchone()
        if row is None:
            return None
        job_id = row[0]
        conn.execute(
            "UPDATE job SET status = 'running', attempts = attempts + 1, started_at = now() "
            "WHERE id = %s", (job_id,))
    try:
        outcome = create_and_run(conn, row[1], row[2], row[3], engine_git_sha=engine_git_sha)
    except Exception as exc:
        with conn.transaction():
            conn.execute(
                "UPDATE job SET status = 'failed', finished_at = now(), error = %s WHERE id = %s",
                (f"{type(exc).__name__}: {exc}", job_id))
        raise
    with conn.transaction():
        conn.execute(
            "UPDATE job SET status = 'done', finished_at = now(), run_id = %s WHERE id = %s",
            (outcome.run_id, job_id))
    return job_id, outcome
