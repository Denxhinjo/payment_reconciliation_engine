"""Persisted runs: the engine's result written to the database, re-verified by the database
finish check, and failures recorded rather than lost."""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import psycopg
import pytest

from recon import engine, importer, runs
from recon.engine import reconcile
from recon.migrate import migrate
from tests.conftest import fresh_database
from tests.scenario import Scenario, d

DEMO = Path(__file__).resolve().parents[2] / "demo-data" / "2026-09"
FILES = {
    "ledger": DEMO / "synthetic_ledger_2026-09.csv",
    "settlement": DEMO / "synthetic_orrery_settlement_2026-09.csv",
    "bank": DEMO / "synthetic_bank_camt053_2026-09.xml",
}


def _import(conn, staff, raws: dict[str, bytes]) -> dict[str, int]:
    ids = {}
    for kind, raw in raws.items():
        stored = importer.store_file(conn, kind, f"synthetic-{kind}", raw, staff)
        importer.process_parse_job(conn, stored.job_id)
        ids[kind] = stored.file_id
    return ids


@pytest.fixture
def staff(conn):
    return importer.seed_synthetic_staff(conn)[0]


@pytest.fixture
def demo_files(conn, staff):
    return _import(conn, staff, {k: p.read_bytes() for k, p in FILES.items()})


def _run(conn, files, **kwargs):
    return runs.create_and_run(conn, files["ledger"], files["settlement"], files["bank"], **kwargs)


def test_demo_run_finishes_and_passes_the_database_finish_check(conn, demo_files):
    outcome = _run(conn, demo_files)
    assert (outcome.status, outcome.matches, outcome.exceptions) == ("finished", 65, 7)
    assert conn.execute("SELECT status FROM reconciliation_run WHERE id = %s",
                        (outcome.run_id,)).fetchone() == ("finished",)


def test_stored_result_is_exactly_the_engines_canonical_bytes(conn, demo_files):
    outcome = _run(conn, demo_files)
    stored, digest = conn.execute(
        "SELECT result_canonical, result_sha256 FROM reconciliation_run WHERE id = %s",
        (outcome.run_id,)).fetchone()
    expected = reconcile(*(p.read_bytes() for p in FILES.values()))
    assert bytes(stored) == expected
    assert bytes(digest).hex() == hashlib.sha256(expected).hexdigest() == outcome.result_sha256


def test_every_input_row_has_one_allocation(conn, demo_files):
    run_id = _run(conn, demo_files).run_id
    for table, total in (("allocation_ledger", 653), ("allocation_settlement", 612), ("allocation_bank", 67)):
        assert conn.execute(f"SELECT count(*) FROM {table} WHERE run_id = %s", (run_id,)).fetchone() == (total,)


def test_persisted_matches_and_exceptions_mirror_the_engine(conn, demo_files):
    run_id = _run(conn, demo_files).run_id
    result = engine.run_engine(*(p.read_bytes() for p in FILES.values()))
    assert conn.execute(
        "SELECT ordinal, pass, explanation FROM run_match WHERE run_id = %s ORDER BY ordinal",
        (run_id,)).fetchall() == [(m.ordinal, m.pass_name, m.explanation) for m in result.matches]
    assert conn.execute(
        "SELECT ordinal, suggested_reason, explanation, encode(fingerprint, 'hex') FROM run_exception "
        "WHERE run_id = %s ORDER BY ordinal", (run_id,)).fetchall() == [
        (e.ordinal, e.reason, e.explanation, e.fingerprint) for e in result.exceptions]


def test_allocations_cite_the_same_rows_as_the_engine(conn, demo_files):
    run_id = _run(conn, demo_files).run_id
    result = engine.run_engine(*(p.read_bytes() for p in FILES.values()))
    rows = conn.execute(
        "SELECT m.ordinal, le.row_number FROM allocation_ledger a "
        "JOIN run_match m ON m.id = a.match_id JOIN ledger_entry le ON le.id = a.ledger_entry_id "
        "WHERE a.run_id = %s", (run_id,)).fetchall()
    persisted: dict[int, list[int]] = {}
    for ordinal, row_number in rows:
        persisted.setdefault(ordinal, []).append(row_number)
    assert {k: sorted(v) for k, v in persisted.items()} == {
        m.ordinal: list(m.ledger) for m in result.matches if m.ledger}


def test_the_database_refuses_an_engine_result_with_wrong_arithmetic(conn, demo_files, monkeypatch):
    """D-012 as a backstop: swap the bank entries of two exact matches (a plausible engine bug).
    Every row is still allocated once, but the amounts no longer agree, so the finish check
    must refuse and the run must be recorded as failed."""
    real = engine.run_engine

    def buggy(*raws):
        result = real(*raws)
        matches = list(result.matches)
        first, second = [i for i, m in enumerate(matches) if m.pass_name == "exact"][:2]
        matches[first], matches[second] = (
            dataclasses.replace(matches[first], bank=matches[second].bank),
            dataclasses.replace(matches[second], bank=matches[first].bank))
        return dataclasses.replace(result, matches=tuple(matches))

    monkeypatch.setattr(runs, "run_engine", buggy)
    outcome = _run(conn, demo_files)
    assert outcome.status == "failed"
    assert "does not satisfy the exact rule" in outcome.error
    assert conn.execute("SELECT count(*) FROM run_match WHERE run_id = %s",
                        (outcome.run_id,)).fetchone() == (0,)


def test_engine_input_error_records_a_failed_run(conn, staff):
    s = Scenario()
    s.payout("ORR-PO-A", d(12), [("PAY-1", 10000)], created_on=d(10), bank_on=d(12))
    s.settlement_line("PAY-2", 5000, "ORR-PO-A", d(13), created_on=d(10))
    ledger, settlement, bank = s.files()
    files = _import(conn, staff, {"ledger": ledger, "settlement": settlement, "bank": bank})
    outcome = _run(conn, files)
    assert outcome.status == "failed" and "inconsistent" in outcome.error
    status, error = conn.execute("SELECT status, error FROM reconciliation_run WHERE id = %s",
                                 (outcome.run_id,)).fetchone()
    assert status == "failed" and "inconsistent" in error


def test_run_refuses_files_parsed_by_another_parser_version(conn, staff, demo_files, monkeypatch):
    monkeypatch.setattr(runs, "PARSER_VERSION", "9.9.9")
    outcome = _run(conn, demo_files)
    assert outcome.status == "failed" and "parsed by parser 1.0.0" in outcome.error


def test_engine_version_and_git_sha_are_recorded(conn, demo_files):
    run_id = _run(conn, demo_files, engine_git_sha="synthetic-sha").run_id
    assert conn.execute("SELECT engine_version, engine_git_sha FROM reconciliation_run WHERE id = %s",
                        (run_id,)).fetchone() == ("1.0.0", "synthetic-sha")


def test_reconcile_job_runs_and_points_at_its_run(conn, demo_files):
    (job_id,) = conn.execute(
        "INSERT INTO job (kind, ledger_file_id, settlement_file_id, bank_file_id) "
        "VALUES ('reconcile', %s, %s, %s) RETURNING id",
        (demo_files["ledger"], demo_files["settlement"], demo_files["bank"])).fetchone()
    processed_job, outcome = runs.process_reconcile_job(conn)
    assert processed_job == job_id and outcome.status == "finished"
    assert conn.execute("SELECT status, run_id FROM job WHERE id = %s", (job_id,)).fetchone() == (
        "done", outcome.run_id)
    assert runs.process_reconcile_job(conn) is None


def test_two_runs_of_the_same_files_store_the_same_result_hash(conn, demo_files):
    first, second = _run(conn, demo_files), _run(conn, demo_files)
    assert first.run_id != second.run_id and first.result_sha256 == second.result_sha256


def test_cli_reconcile_end_to_end(capsys):
    from recon.__main__ import main

    with fresh_database() as url:
        migrate(url)
        main(["seed-staff", "--database-url", url])
        for kind, path in FILES.items():
            main(["import", "--database-url", url, "--kind", kind, "--file", str(path)])
        capsys.readouterr()
        assert main(["reconcile", "--database-url", url, "--ledger-file", "1",
                     "--settlement-file", "2", "--bank-file", "3"]) == 0
        out = capsys.readouterr().out
        assert "run #1 finished: 65 matches, 7 exceptions" in out
        with psycopg.connect(url) as conn:
            assert conn.execute("SELECT status FROM reconciliation_run").fetchall() == [("finished",)]


# --- edge cases persisted: the database finish check as a second, independent layer -----------
#
# Each scenario sits one cent or one rule away from a match. The correct engine classifies it
# as an exception and the run finishes. An engine that wrongly matched it (a tolerance, a
# partial batch, a debit accepted as a payout, a reference ignored) would hand the database a
# match whose arithmetic or references do not hold, and the finish check would refuse the run,
# so these tests would fail through a second layer independent of the engine tests (D-064).

def _fee_one_cent_short(s):
    s.payout("ORR-PO-A", d(12), [("PAY-1", 10000)], created_on=d(10), bank_on=d(12), bank_delta=-1)


def _partial_batch(s):
    s.ledger_row("PAY-1", 10000, d(10))
    s.ledger_row("PAY-2", 5000, d(10))
    s.payout("ORR-PO-B", d(12), [("PAY-1", 10000), ("PAY-2", 5000), ("PAY-3", 7000)],
             created_on=d(10), bank_on=d(12), ledger=False)


def _batch_one_cent_short(s):
    s.payout("ORR-PO-B", d(12), [("PAY-1", 10000), ("PAY-2", 5000)], created_on=d(10),
             bank_on=d(12), bank_delta=-1)


def _transfer_one_cent_short(s):
    s.transfer("PAY-1", 12000, d(10), bank_on=d(10), bank_amount=11999)


def _transfer_other_reference(s):
    s.ledger_row("PAY-1", 12000, d(10), channel="bank_transfer")
    s.bank_entry(12000, d(10), ustrd="PAY-2")


def _refund_payout_debited(s):
    s.ledger_row("PAY-1-R1", -2000, d(10))
    s.settlement_line("PAY-1-R1", -2000, "ORR-PO-R", d(12), created_on=d(10))
    s.bank_entry(-2000, d(12), end_to_end_id="ORR-PO-R", ustrd="ORRERY DEBIT")


@pytest.mark.parametrize("build, expected_reasons", [
    (_fee_one_cent_short, ["amount_mismatch"]),
    (_partial_batch, ["missing_from_ledger"]),
    (_batch_one_cent_short, ["amount_mismatch"]),
    (_transfer_one_cent_short, ["amount_mismatch"]),
    (_transfer_other_reference, ["missing_from_bank", "unknown_deposit"]),
    (_refund_payout_debited, ["missing_from_bank", "unexplained_debit"]),
], ids=["fee-1-cent-short", "partial-batch", "batch-1-cent-short", "transfer-1-cent-short",
        "transfer-other-reference", "refund-payout-debited"])
def test_near_miss_is_persisted_as_an_exception_and_the_run_finishes(conn, staff, build, expected_reasons):
    s = Scenario()
    build(s)
    ledger, settlement, bank = s.files()
    files = _import(conn, staff, {"ledger": ledger, "settlement": settlement, "bank": bank})
    outcome = _run(conn, files)
    assert outcome.status == "finished", outcome.error
    reasons = [r for (r,) in conn.execute(
        "SELECT e.suggested_reason FROM run_exception e WHERE e.run_id = %s ORDER BY e.ordinal",
        (outcome.run_id,))]
    # The filler payout (added by Scenario for transfer-only cases) is the only match allowed.
    passes = [p for (p,) in conn.execute(
        "SELECT pass FROM run_match WHERE run_id = %s", (outcome.run_id,))]
    assert reasons == expected_reasons
    assert passes in ([], ["gross_net"])
