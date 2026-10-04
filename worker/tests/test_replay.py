"""Replay (design §6): a stored run recomputed from its stored raw files reproduces the original
byte for byte, independently of database ids and import order. A mismatch is recorded, not
refused; a run of another engine version is refused with instructions."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import psycopg
import pytest

from recon import engine, importer, runs
from recon.engine import ENGINE_VERSION
from recon.migrate import migrate
from tests.conftest import fresh_database

DEMO = Path(__file__).resolve().parents[2] / "demo-data" / "2026-09"
FILES = {
    "ledger": DEMO / "synthetic_ledger_2026-09.csv",
    "settlement": DEMO / "synthetic_orrery_settlement_2026-09.csv",
    "bank": DEMO / "synthetic_bank_camt053_2026-09.xml",
}
GOLDEN = json.loads((Path(__file__).parent / "golden" / "results.json").read_text())
(GOLDEN_SHA,) = GOLDEN[ENGINE_VERSION].values()


def _import_and_run(conn, order=("ledger", "settlement", "bank")) -> int:
    staff = importer.seed_synthetic_staff(conn)[0]
    ids = {}
    for kind in order:
        stored = importer.store_file(conn, kind, FILES[kind].name, FILES[kind].read_bytes(), staff)
        importer.process_parse_job(conn, stored.job_id)
        ids[kind] = stored.file_id
    outcome = runs.create_and_run(conn, ids["ledger"], ids["settlement"], ids["bank"])
    assert outcome.status == "finished"
    return outcome.run_id


@pytest.fixture
def run_id(conn):
    return _import_and_run(conn)


def _overview(conn, run_id):
    return conn.execute(
        "SELECT replay_of_run_id, replay_identical, result_sha256, status FROM run_overview "
        "WHERE run_id = %s", (run_id,)).fetchone()


def test_replay_reproduces_the_run_byte_for_byte(conn, run_id):
    outcome = runs.replay(conn, run_id)
    assert outcome.identical and outcome.status == "finished"
    assert outcome.replay_sha256 == outcome.original_sha256 == GOLDEN_SHA
    original, replayed = (bytes(conn.execute(
        "SELECT result_canonical FROM reconciliation_run WHERE id = %s", (rid,)).fetchone()[0])
        for rid in (run_id, outcome.replay_run_id))
    assert original == replayed


def test_replay_is_recorded_as_a_linked_run_and_shown_as_identical(conn, run_id):
    outcome = runs.replay(conn, run_id)
    assert _overview(conn, outcome.replay_run_id) == (run_id, True, GOLDEN_SHA, "finished")
    assert _overview(conn, run_id)[:2] == (None, None)


def test_a_replay_of_a_replay_is_still_identical(conn, run_id):
    first = runs.replay(conn, run_id)
    second = runs.replay(conn, first.replay_run_id)
    assert second.identical and second.replay_sha256 == GOLDEN_SHA


def test_result_is_independent_of_database_ids_and_import_order():
    """Two fresh databases, files imported in opposite orders (so every id differs): both runs
    store the same bytes, equal to the golden hash."""
    hashes = []
    for order in (("ledger", "settlement", "bank"), ("bank", "settlement", "ledger")):
        with fresh_database() as url:
            migrate(url)
            with psycopg.connect(url, autocommit=True) as conn:
                run_id = _import_and_run(conn, order)
                file_ids = conn.execute(
                    "SELECT ledger_file_id, bank_file_id FROM reconciliation_run WHERE id = %s",
                    (run_id,)).fetchone()
                hashes.append((file_ids, _overview(conn, run_id)[2]))
    (ids_a, sha_a), (ids_b, sha_b) = hashes
    assert ids_a != ids_b
    assert sha_a == sha_b == GOLDEN_SHA


def test_a_differing_replay_is_recorded_not_refused(conn, run_id, monkeypatch):
    """Simulate an engine whose output drifted without a version bump."""
    real = engine.run_engine

    def drifted(*raws):
        result = real(*raws)
        first = dataclasses.replace(result.exceptions[0],
                                    explanation=result.exceptions[0].explanation + " (drifted)")
        return dataclasses.replace(result, exceptions=(first, *result.exceptions[1:]))

    monkeypatch.setattr(runs, "run_engine", drifted)
    outcome = runs.replay(conn, run_id)
    assert outcome.status == "finished" and not outcome.identical
    assert outcome.replay_sha256 != outcome.original_sha256 == GOLDEN_SHA
    assert _overview(conn, outcome.replay_run_id)[:2] == (run_id, False)
    # The original is untouched.
    assert _overview(conn, run_id)[2] == GOLDEN_SHA


def test_a_failed_replay_is_recorded_as_not_identical(conn, run_id, monkeypatch):
    def broken(*raws):
        raise engine.EngineInputError("synthetic failure during replay")

    monkeypatch.setattr(runs, "run_engine", broken)
    outcome = runs.replay(conn, run_id)
    assert (outcome.status, outcome.identical) == ("failed", False)
    assert _overview(conn, outcome.replay_run_id)[1] is False


def test_replay_of_another_engine_version_is_refused_with_the_tag_to_use(conn, run_id, monkeypatch):
    monkeypatch.setattr(runs, "ENGINE_VERSION", "1.1.0")
    with pytest.raises(runs.ReplayRefused,
                       match=r"engine 1\.0\.0, but this is engine 1\.1\.0\. Check out git tag engine-v1\.0\.0"):
        runs.replay(conn, run_id)
    assert conn.execute("SELECT count(*) FROM reconciliation_run").fetchone() == (1,)


def test_replay_of_a_failed_or_missing_run_is_refused(conn, build):
    month = build.standard_month()
    failed = build.run(month.ledger_file, month.settlement_file, month.bank_file)
    conn.execute("UPDATE reconciliation_run SET status = 'failed', finished_at = now(), "
                 "error = 'synthetic' WHERE id = %s", (failed,))
    with pytest.raises(runs.ReplayRefused, match="is failed; only finished runs"):
        runs.replay(conn, failed)
    with pytest.raises(runs.ReplayRefused, match="no run with id 987654321"):
        runs.replay(conn, 987654321)


def test_replay_job_runs_and_points_at_the_replay(conn, run_id):
    (job_id,) = conn.execute("INSERT INTO job (kind, replay_of_run_id) VALUES ('replay', %s) RETURNING id",
                             (run_id,)).fetchone()
    processed, outcome = runs.process_replay_job(conn)
    assert processed == job_id and outcome.identical
    assert conn.execute("SELECT status, run_id FROM job WHERE id = %s", (job_id,)).fetchone() == (
        "done", outcome.replay_run_id)


def test_refused_replay_job_fails_with_the_reason(conn, run_id, monkeypatch):
    monkeypatch.setattr(runs, "ENGINE_VERSION", "2.0.0")
    (job_id,) = conn.execute("INSERT INTO job (kind, replay_of_run_id) VALUES ('replay', %s) RETURNING id",
                             (run_id,)).fetchone()
    _, reason = runs.process_replay_job(conn)
    assert "engine-v1.0.0" in reason
    status, error = conn.execute("SELECT status, error FROM job WHERE id = %s", (job_id,)).fetchone()
    assert status == "failed" and "engine-v1.0.0" in error


def test_cli_replay(capsys):
    from recon.__main__ import main

    with fresh_database() as url:
        migrate(url)
        with psycopg.connect(url, autocommit=True) as conn:
            run_id = _import_and_run(conn)
        assert main(["replay", "--database-url", url, "--run", str(run_id)]) == 0
        out = capsys.readouterr().out
        assert "IDENTICAL" in out and GOLDEN_SHA in out
        assert main(["replay", "--database-url", url, "--run", "999"]) == 1
        assert "no run with id 999" in capsys.readouterr().err
