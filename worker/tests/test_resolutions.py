"""Resolutions: mandatory note, append-only, corrected only by a superseding row, one linear
chain per exception, and only for exceptions of finished runs."""

from __future__ import annotations

import pytest

from tests.conftest import (
    CHECK_VIOLATION, FOREIGN_KEY_VIOLATION, RUN_NOT_FINISHED, UNIQUE_VIOLATION, raises_sqlstate,
)
from tests.factories import STANDARD_PLAN

NOTE = "Synthetic: confirmed with processor support, payout delayed."


@pytest.fixture
def finished(build):
    month, run_id, ids = build.finished_standard_run()
    # ids[4] is the missing_from_bank exception, ids[5] the unknown_deposit exception.
    return month, ids[4], ids[5]


def _resolve(conn, exception_id, staff, *, note=NOTE, reason="timing_clears_next_period",
             supersedes=None):
    return conn.execute(
        "INSERT INTO resolution (exception_id, supersedes_id, reason_code, note, resolved_by) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (exception_id, supersedes, reason, note, staff),
    ).fetchone()[0]


def test_first_resolution_is_accepted(conn, finished):
    month, exception_id, _ = finished
    _resolve(conn, exception_id, month.staff)


@pytest.mark.parametrize("note", ["", "          ", "ok", "  fine.   ", "123456789"])
def test_note_shorter_than_ten_non_blank_characters_is_rejected(conn, finished, note):
    month, exception_id, _ = finished
    with raises_sqlstate(conn, CHECK_VIOLATION):
        _resolve(conn, exception_id, month.staff, note=note)


def test_note_of_exactly_ten_characters_is_accepted(conn, finished):
    month, exception_id, _ = finished
    _resolve(conn, exception_id, month.staff, note="  abcdefghij  ")


def test_unknown_reason_code_is_rejected(conn, finished):
    month, exception_id, _ = finished
    with raises_sqlstate(conn, FOREIGN_KEY_VIOLATION):
        _resolve(conn, exception_id, month.staff, reason="looked_fine_to_me")


def test_second_first_resolution_for_the_same_exception_is_rejected(conn, finished):
    """A second resolution must supersede the first; it cannot sit beside it."""
    month, exception_id, _ = finished
    _resolve(conn, exception_id, month.staff)
    with raises_sqlstate(conn, UNIQUE_VIOLATION):
        _resolve(conn, exception_id, month.staff, reason="other_see_note")


def test_correction_by_superseding_resolution_keeps_both(conn, finished):
    month, exception_id, _ = finished
    first = _resolve(conn, exception_id, month.staff)
    second = _resolve(conn, exception_id, month.staff, reason="processor_error_claim_raised",
                      note="Synthetic: correction, processor confirmed it lost the payout.",
                      supersedes=first)
    third = _resolve(conn, exception_id, month.staff, reason="funds_identified_and_posted",
                     note="Synthetic: processor re-sent the payout; posted on 2026-10-05.",
                     supersedes=second)
    history = conn.execute(
        "SELECT id FROM resolution WHERE exception_id = %s ORDER BY id", (exception_id,)
    ).fetchall()
    assert [row[0] for row in history] == [first, second, third]
    current = conn.execute(
        "SELECT id FROM current_resolution WHERE exception_id = %s", (exception_id,)
    ).fetchall()
    assert current == [(third,)]


def test_a_resolution_can_be_superseded_only_once(conn, finished):
    month, exception_id, _ = finished
    first = _resolve(conn, exception_id, month.staff)
    _resolve(conn, exception_id, month.staff, supersedes=first, reason="other_see_note")
    with raises_sqlstate(conn, UNIQUE_VIOLATION):
        _resolve(conn, exception_id, month.staff, supersedes=first, reason="bank_error_raised")


def test_cannot_supersede_a_resolution_of_another_exception(conn, finished):
    month, exception_a, exception_b = finished
    resolution_a = _resolve(conn, exception_a, month.staff)
    with raises_sqlstate(conn, FOREIGN_KEY_VIOLATION):
        _resolve(conn, exception_b, month.staff, supersedes=resolution_a)


def test_resolution_requires_a_staff_user(conn, finished):
    _, exception_id, _ = finished
    with raises_sqlstate(conn, FOREIGN_KEY_VIOLATION):
        _resolve(conn, exception_id, 999_999_999)


def test_exception_of_a_running_run_cannot_be_resolved(conn, build):
    month = build.standard_month()
    run_id = build.run(month.ledger_file, month.settlement_file, month.bank_file)
    ids = build.allocate(run_id, month, STANDARD_PLAN)
    with raises_sqlstate(conn, RUN_NOT_FINISHED):
        _resolve(conn, ids[4], month.staff)
