"""Runs and allocations: each input row consumed at most once per run, only from the run's own
files, and only while the run is running."""

from __future__ import annotations

import hashlib

from tests.conftest import (
    CHECK_VIOLATION, FOREIGN_KEY_VIOLATION, RUN_STATE, UNIQUE_VIOLATION, raises_sqlstate,
)
from tests.factories import STANDARD_PLAN


def _run(build, month):
    return build.run(month.ledger_file, month.settlement_file, month.bank_file)


# --- run slots ----------------------------------------------------------------------------

def test_run_slot_rejects_a_file_of_the_wrong_kind(conn, build):
    month = build.standard_month()
    with raises_sqlstate(conn, FOREIGN_KEY_VIOLATION):
        build.run(month.bank_file, month.settlement_file, month.bank_file)


def test_run_cannot_use_an_unparsed_file(conn, build):
    month = build.standard_month()
    unparsed_ledger = build.file("ledger")
    with raises_sqlstate(conn, FOREIGN_KEY_VIOLATION):
        build.run(unparsed_ledger, month.settlement_file, month.bank_file)


def test_run_cannot_use_a_rejected_file(conn, build):
    month = build.standard_month()
    rejected = build.file("bank")
    build.parse(rejected, "bank", status="rejected", error="XSD validation failed")
    with raises_sqlstate(conn, FOREIGN_KEY_VIOLATION):
        build.run(month.ledger_file, month.settlement_file, rejected)


def test_engine_version_must_be_semver(conn, build):
    month = build.standard_month()
    with raises_sqlstate(conn, CHECK_VIOLATION):
        conn.execute(
            "INSERT INTO reconciliation_run (engine_version, ledger_file_id, settlement_file_id, "
            "bank_file_id) VALUES ('latest', %s, %s, %s)",
            (month.ledger_file, month.settlement_file, month.bank_file),
        )


# --- run lifecycle ------------------------------------------------------------------------

def test_run_must_be_created_running(conn, build):
    month = build.standard_month()
    with raises_sqlstate(conn, RUN_STATE):
        build.run(month.ledger_file, month.settlement_file, month.bank_file, status="failed",
                  error="x", finished_at="2026-10-01T00:00:00Z")


def test_finished_requires_a_result(conn, build):
    month = build.standard_month()
    run_id = _run(build, month)
    build.allocate(run_id, month, STANDARD_PLAN)
    with raises_sqlstate(conn, CHECK_VIOLATION):
        conn.execute(
            "UPDATE reconciliation_run SET status = 'finished', finished_at = now() WHERE id = %s",
            (run_id,),
        )


def test_failed_requires_an_error(conn, build):
    month = build.standard_month()
    run_id = _run(build, month)
    with raises_sqlstate(conn, CHECK_VIOLATION):
        conn.execute(
            "UPDATE reconciliation_run SET status = 'failed', finished_at = now() WHERE id = %s",
            (run_id,),
        )


def test_failed_run_records_its_error(conn, build):
    month = build.standard_month()
    run_id = _run(build, month)
    conn.execute(
        "UPDATE reconciliation_run SET status = 'failed', finished_at = now(), "
        "error = 'input row 3 is not EUR' WHERE id = %s",
        (run_id,),
    )


def test_finished_run_cannot_go_back_to_running(conn, build):
    _, run_id, _ = build.finished_standard_run()
    with raises_sqlstate(conn, RUN_STATE):
        conn.execute(
            "UPDATE reconciliation_run SET status = 'running', finished_at = NULL, "
            "result_canonical = NULL WHERE id = %s",
            (run_id,),
        )


def test_running_run_identity_cannot_change(conn, build):
    month = build.standard_month()
    run_id = _run(build, month)
    with raises_sqlstate(conn, RUN_STATE):
        conn.execute(
            "UPDATE reconciliation_run SET engine_version = '9.9.9' WHERE id = %s", (run_id,)
        )


def test_result_sha256_is_the_hash_of_the_stored_result(conn, build):
    _, run_id, _ = build.finished_standard_run()
    result, digest = conn.execute(
        "SELECT result_canonical, result_sha256 FROM reconciliation_run WHERE id = %s", (run_id,)
    ).fetchone()
    assert bytes(digest) == hashlib.sha256(bytes(result)).digest()


def test_replay_run_may_allocate_the_same_rows_again(conn, build):
    """Consumption is per run: a replay legitimately re-allocates every row."""
    month, run_id, _ = build.finished_standard_run()
    replay_id = build.run(month.ledger_file, month.settlement_file, month.bank_file,
                          replay_of_run_id=run_id)
    build.allocate(replay_id, month, STANDARD_PLAN)
    build.finish(replay_id)


# --- allocations --------------------------------------------------------------------------

def test_a_row_cannot_be_consumed_by_two_matches_in_one_run(conn, build):
    month = build.standard_month()
    run_id = _run(build, month)
    first = build.match(run_id, 1, "exact")
    second = build.match(run_id, 2, "exact")
    build.alloc_ledger(run_id, month.ledger_file, month.ledger["L1"], match=first)
    with raises_sqlstate(conn, UNIQUE_VIOLATION):
        build.alloc_ledger(run_id, month.ledger_file, month.ledger["L1"], match=second)


def test_a_row_cannot_be_both_matched_and_an_exception(conn, build):
    month = build.standard_month()
    run_id = _run(build, month)
    match_id = build.match(run_id, 1, "exact")
    exception_id = build.exception(run_id, 2, "missing_from_bank")
    with raises_sqlstate(conn, CHECK_VIOLATION):
        build.alloc_ledger(run_id, month.ledger_file, month.ledger["L1"],
                           match=match_id, exception=exception_id)


def test_an_allocation_must_point_somewhere(conn, build):
    month = build.standard_month()
    run_id = _run(build, month)
    with raises_sqlstate(conn, CHECK_VIOLATION):
        build.alloc_bank(run_id, month.bank_file, month.bank["B1"])


def test_cannot_allocate_a_row_from_another_file(conn, build):
    """A run cannot consume a row from a file that is not in its ledger slot."""
    month = build.standard_month()
    run_id = _run(build, month)
    other_file = build.parsed_file("ledger")
    foreign_row = build.ledger(other_file, "PAY-1", 12000, channel="bank_transfer")
    match_id = build.match(run_id, 1, "exact")
    # Claiming the run's file id while citing the other file's row:
    with raises_sqlstate(conn, FOREIGN_KEY_VIOLATION):
        build.alloc_ledger(run_id, month.ledger_file, foreign_row, match=match_id)
    # Claiming the other file's id, which is not the run's ledger file:
    with raises_sqlstate(conn, FOREIGN_KEY_VIOLATION):
        build.alloc_ledger(run_id, other_file, foreign_row, match=match_id)


def test_cannot_allocate_to_a_match_of_another_run(conn, build):
    month = build.standard_month()
    run_a = _run(build, month)
    run_b = _run(build, month)
    match_in_b = build.match(run_b, 1, "exact")
    with raises_sqlstate(conn, FOREIGN_KEY_VIOLATION):
        build.alloc_ledger(run_a, month.ledger_file, month.ledger["L1"], match=match_in_b)


def test_match_ordinal_unique_within_a_run(conn, build):
    month = build.standard_month()
    run_id = _run(build, month)
    build.match(run_id, 1, "exact")
    with raises_sqlstate(conn, UNIQUE_VIOLATION):
        build.match(run_id, 1, "gross_net")


def test_unknown_pass_is_rejected(conn, build):
    month = build.standard_month()
    run_id = _run(build, month)
    with raises_sqlstate(conn, CHECK_VIOLATION):
        build.match(run_id, 1, "fuzzy")


def test_unknown_suggested_reason_is_rejected(conn, build):
    month = build.standard_month()
    run_id = _run(build, month)
    with raises_sqlstate(conn, CHECK_VIOLATION):
        build.exception(run_id, 1, "probably_fine")


def test_all_seven_suggested_reasons_are_accepted(conn, build):
    month = build.standard_month()
    run_id = _run(build, month)
    reasons = ["missing_from_bank", "amount_mismatch", "timing", "unknown_deposit",
               "possible_duplicate", "missing_from_ledger", "unexplained_debit"]
    for ordinal, reason in enumerate(reasons, start=1):
        build.exception(run_id, ordinal, reason)


def test_nothing_can_be_added_to_a_finished_run(conn, build):
    month, run_id, _ = build.finished_standard_run()
    with raises_sqlstate(conn, RUN_STATE):
        build.match(run_id, 99, "exact")
    with raises_sqlstate(conn, RUN_STATE):
        build.exception(run_id, 99, "timing")


def test_nothing_can_be_added_to_a_failed_run(conn, build):
    month = build.standard_month()
    run_id = _run(build, month)
    conn.execute(
        "UPDATE reconciliation_run SET status = 'failed', finished_at = now(), "
        "error = 'synthetic failure' WHERE id = %s",
        (run_id,),
    )
    with raises_sqlstate(conn, RUN_STATE):
        build.exception(run_id, 1, "timing")
