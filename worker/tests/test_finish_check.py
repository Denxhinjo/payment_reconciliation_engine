"""The database independently re-verifies a run's output when it finishes (D-012).

Each test hands the database an allocation that a buggy engine could produce, and asserts
that the transition to 'finished' is refused with RC003. The baseline test proves the
correct allocation of the same month is accepted, so the refusals are caused by the specific
defect in each test and not by the scenario itself.
"""

from __future__ import annotations

from tests.conftest import FINISH_CHECK, raises_sqlstate
from tests.factories import STANDARD_PLAN


def _assert_finish_refused(conn, build, month, plan):
    run_id = build.run(month.ledger_file, month.settlement_file, month.bank_file)
    build.allocate(run_id, month, plan)
    with raises_sqlstate(conn, FINISH_CHECK):
        build.finish(run_id)
    (status,) = conn.execute(
        "SELECT status FROM reconciliation_run WHERE id = %s", (run_id,)
    ).fetchone()
    assert status == "running"


def _replace(plan, old_entry, *new_entries):
    index = plan.index(old_entry)
    return plan[:index] + list(new_entries) + plan[index + 1:]


EXACT = STANDARD_PLAN[0]
GROSS_NET = STANDARD_PLAN[1]
MANY_TO_ONE = STANDARD_PLAN[2]


def test_correct_allocation_finishes(conn, build):
    _, run_id, _ = build.finished_standard_run()
    (status,) = conn.execute(
        "SELECT status FROM reconciliation_run WHERE id = %s", (run_id,)
    ).fetchone()
    assert status == "finished"


# --- completeness -------------------------------------------------------------------------

def test_unallocated_bank_row_blocks_finish(conn, build):
    month = build.standard_month()
    plan = [entry for entry in STANDARD_PLAN if entry[1] != "unknown_deposit"]
    _assert_finish_refused(conn, build, month, plan)


def test_unallocated_ledger_row_blocks_finish(conn, build):
    month = build.standard_month()
    plan = [entry for entry in STANDARD_PLAN if entry[1] != "missing_from_bank"]
    _assert_finish_refused(conn, build, month, plan)


def test_unallocated_settlement_row_blocks_finish(conn, build):
    month = build.standard_month()
    month.settlement["S9"] = build.settlement(month.settlement_file, "PAY-9", 2000, 53,
                                              payout_reference="ORR-PO-C")
    _assert_finish_refused(conn, build, month, STANDARD_PLAN)


def test_match_citing_no_rows_blocks_finish(conn, build):
    month = build.standard_month()
    plan = STANDARD_PLAN + [("match", "exact", (), (), ())]
    _assert_finish_refused(conn, build, month, plan)


def test_exception_citing_no_rows_blocks_finish(conn, build):
    month = build.standard_month()
    plan = STANDARD_PLAN + [("exception", "timing", (), (), ())]
    _assert_finish_refused(conn, build, month, plan)


# --- pass: exact --------------------------------------------------------------------------

def test_exact_with_different_amounts_blocks_finish(conn, build):
    month = build.standard_month()
    month.bank["B5"] = build.bank(month.bank_file, 11900, ustrd="PAY-1")
    plan = _replace(STANDARD_PLAN, EXACT,
                    ("match", "exact", ("L1",), (), ("B5",)),
                    ("exception", "unknown_deposit", (), (), ("B1",)))
    _assert_finish_refused(conn, build, month, plan)


def test_exact_with_different_reference_blocks_finish(conn, build):
    month = build.standard_month()
    month.bank["B5"] = build.bank(month.bank_file, 12000, ustrd="PAY-999")
    plan = _replace(STANDARD_PLAN, EXACT,
                    ("match", "exact", ("L1",), (), ("B5",)),
                    ("exception", "unknown_deposit", (), (), ("B1",)))
    _assert_finish_refused(conn, build, month, plan)


def test_exact_with_two_bank_rows_blocks_finish(conn, build):
    month = build.standard_month()
    plan = _replace(STANDARD_PLAN, EXACT, ("match", "exact", ("L1",), (), ("B1", "B4")))
    plan = [entry for entry in plan if entry[1] != "unknown_deposit"]
    _assert_finish_refused(conn, build, month, plan)


def test_exact_across_currencies_blocks_finish(conn, build):
    month = build.standard_month()
    month.bank["B5"] = build.bank(month.bank_file, 12000, ustrd="PAY-1", currency="SEK")
    plan = _replace(STANDARD_PLAN, EXACT,
                    ("match", "exact", ("L1",), (), ("B5",)),
                    ("exception", "unknown_deposit", (), (), ("B1",)))
    _assert_finish_refused(conn, build, month, plan)


# --- pass: gross_net ----------------------------------------------------------------------

def test_gross_net_with_fee_mismatch_blocks_finish(conn, build):
    """Planted P2 shape: report says fee 1.65, but the bank received 0.40 less than net."""
    month = build.standard_month()
    month.bank["B5"] = build.bank(month.bank_file, 9835 - 40, end_to_end_id="ORR-PO-A")
    plan = _replace(STANDARD_PLAN, GROSS_NET,
                    ("match", "gross_net", ("L2",), ("S1",), ("B5",)),
                    ("exception", "unknown_deposit", (), (), ("B2",)))
    _assert_finish_refused(conn, build, month, plan)


def test_gross_net_with_bank_entry_of_another_payout_blocks_finish(conn, build):
    month = build.standard_month()
    month.bank["B5"] = build.bank(month.bank_file, 9835, end_to_end_id="ORR-PO-Z")
    plan = _replace(STANDARD_PLAN, GROSS_NET,
                    ("match", "gross_net", ("L2",), ("S1",), ("B5",)),
                    ("exception", "unknown_deposit", (), (), ("B2",)))
    _assert_finish_refused(conn, build, month, plan)


def test_gross_net_with_ledger_for_another_payment_blocks_finish(conn, build):
    month = build.standard_month()
    month.ledger["L6"] = build.ledger(month.ledger_file, "PAY-6", 10000)
    plan = _replace(STANDARD_PLAN, GROSS_NET,
                    ("match", "gross_net", ("L6",), ("S1",), ("B2",)),
                    ("exception", "missing_from_bank", ("L2",), (), ()))
    _assert_finish_refused(conn, build, month, plan)


def test_gross_net_against_a_bank_debit_blocks_finish(conn, build):
    """Every sum agrees, but a payout is money in: a debit cannot satisfy it."""
    month = build.standard_month()
    month.ledger["L7"] = build.ledger(month.ledger_file, "PAY-2-R1", -2000)
    month.settlement["S7"] = build.settlement(month.settlement_file, "PAY-2-R1", -2000, 0,
                                              payout_reference="ORR-PO-R")
    month.bank["B7"] = build.bank(month.bank_file, -2000, end_to_end_id="ORR-PO-R")
    plan = STANDARD_PLAN + [("match", "gross_net", ("L7",), ("S7",), ("B7",))]
    _assert_finish_refused(conn, build, month, plan)


# --- pass: many_to_one --------------------------------------------------------------------

def test_many_to_one_with_sum_mismatch_blocks_finish(conn, build):
    month = build.standard_month()
    month.bank["B5"] = build.bank(month.bank_file, 11782 - 1, end_to_end_id="ORR-PO-B")
    plan = _replace(STANDARD_PLAN, MANY_TO_ONE,
                    ("match", "many_to_one", ("L3", "L4"), ("S2", "S3"), ("B5",)),
                    ("exception", "unknown_deposit", (), (), ("B3",)))
    _assert_finish_refused(conn, build, month, plan)


def test_many_to_one_with_a_single_payment_blocks_finish(conn, build):
    """One payment is pass gross_net, not many_to_one: the pass label must be truthful."""
    month = build.standard_month()
    plan = _replace(STANDARD_PLAN, GROSS_NET,
                    ("match", "many_to_one", ("L2",), ("S1",), ("B2",)))
    _assert_finish_refused(conn, build, month, plan)


def test_many_to_one_spanning_two_payouts_blocks_finish(conn, build):
    month = build.standard_month()
    month.bank["B5"] = build.bank(month.bank_file, 9835 + 4905, end_to_end_id="ORR-PO-A")
    plan = [
        EXACT,
        ("match", "many_to_one", ("L2", "L3"), ("S1", "S2"), ("B5",)),
        ("exception", "missing_from_bank", ("L4", "L5"), ("S3",), ()),
        ("exception", "unknown_deposit", (), (), ("B2", "B3", "B4")),
    ]
    _assert_finish_refused(conn, build, month, plan)


def test_many_to_one_with_an_extra_ledger_row_blocks_finish(conn, build):
    month = build.standard_month()
    month.ledger["L6"] = build.ledger(month.ledger_file, "PAY-6", 1)
    plan = _replace(STANDARD_PLAN, MANY_TO_ONE,
                    ("match", "many_to_one", ("L3", "L4", "L6"), ("S2", "S3"), ("B3",)))
    _assert_finish_refused(conn, build, month, plan)
