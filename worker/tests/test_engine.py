"""Matching engine: the planted demo month, each pass's rules and boundaries, classification of
leftovers (including D-048 timing deadlines), duplicates, and canonical output."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from recon.engine import (
    EngineInputError, _day, canonical_json, reconcile, run_engine,
)
from recon.parse.bank import parse_bank
from recon.parse.csvfiles import parse_ledger, parse_settlement
from tests.scenario import Scenario, d

DEMO = Path(__file__).resolve().parents[2] / "demo-data" / "2026-09"
DEMO_FILES = tuple((DEMO / name).read_bytes() for name in (
    "synthetic_ledger_2026-09.csv", "synthetic_orrery_settlement_2026-09.csv",
    "synthetic_bank_camt053_2026-09.xml"))
DEADLINE = re.compile(r"by \d{1,2} [A-Z][a-z]{2} \d{4}")


@pytest.fixture(scope="module")
def demo():
    return run_engine(*DEMO_FILES)


# --- the planted demo month ---------------------------------------------------------------------

def _planted_items():
    planted = json.loads((DEMO / "planted.json").read_text())
    return planted["planted"] + planted["natural_timing"]


def _natural_keys():
    ledger = {r.entry_id: r.row_number for r in parse_ledger(DEMO_FILES[0])}
    settlement = {s.balance_transaction_id: s.row_number for s in parse_settlement(DEMO_FILES[1])}
    bank = {e.acct_svcr_ref: e.entry_index for e in parse_bank(DEMO_FILES[2]).entries}
    return ledger, settlement, bank


@pytest.mark.parametrize("item", _planted_items(), ids=lambda item: item["id"])
def test_demo_month_planted_item_has_exactly_its_expected_outcome(demo, item):
    ledger, settlement, bank = _natural_keys()
    expected = item["expected"]
    keys = (tuple(sorted(ledger[i] for i in expected["ledger"])),
            tuple(sorted(settlement[i] for i in expected["settlement"])),
            tuple(sorted(bank[i] for i in expected["bank"])))
    if expected["kind"] == "match":
        found = [m for m in demo.matches if (m.ledger, m.settlement, m.bank) == keys]
        assert [m.pass_name for m in found] == [expected["pass"]]
    else:
        found = [e for e in demo.exceptions if (e.ledger, e.settlement, e.bank) == keys]
        assert [e.reason for e in found] == [expected["reason"]]


def test_demo_month_has_no_other_exceptions(demo):
    """Five planted problems (four are exceptions; P3 is a match) and three timing items."""
    assert len(demo.exceptions) == 7
    assert sorted(e.reason for e in demo.exceptions) == [
        "amount_mismatch", "missing_from_bank", "possible_duplicate", "timing", "timing",
        "timing", "unknown_deposit"]


def test_demo_month_match_counts_per_pass(demo):
    passes = [m.pass_name for m in demo.matches]
    assert (passes.count("exact"), passes.count("gross_net"), passes.count("many_to_one")) == (39, 3, 23)


def test_demo_p2_explanation_states_the_unexplained_difference(demo):
    (p2,) = [e for e in demo.exceptions if e.reason == "amount_mismatch"]
    assert "stated fees EUR 4.69" in p2.explanation and "unexplained EUR 0.40" in p2.explanation


def test_demo_p4_explanation_cites_the_matched_original(demo):
    (p4,) = [e for e in demo.exceptions if e.reason == "possible_duplicate"]
    assert "LE-000527" in p4.explanation and "LE-000526" in p4.explanation


def test_demo_timing_explanations_state_their_deadlines(demo):
    timing = [e.explanation for e in demo.exceptions if e.reason == "timing"]
    assert "expected at the bank by 4 Oct 2026" in timing[0]
    assert "expected at the bank by 5 Oct 2026" in timing[1]
    assert all("treat as missing" in t for t in timing)


def test_every_demo_row_is_allocated_exactly_once(demo):
    for field, total in (("ledger", 653), ("settlement", 612), ("bank", 67)):
        cited = [k for item in (*demo.matches, *demo.exceptions) for k in getattr(item, field)]
        assert sorted(cited) == list(range(1, total + 1))


# --- pass A: exact ------------------------------------------------------------------------------

def test_exact_matches_same_reference_amount_within_window():
    s = Scenario()
    entry, bank = s.transfer("PAY-1", 12000, d(10), bank_on=d(10))
    outcome = s.run()
    assert outcome.matches == [{"pass": "exact", "ledger": [entry], "settlement": [], "bank": [bank],
                                "explanation": outcome.matches[0]["explanation"]}]
    assert outcome.exceptions == []


def test_exact_window_last_day_still_matches():
    s = Scenario()
    s.transfer("PAY-1", 12000, d(10), bank_on=d(15))
    assert s.run().match_passes() == ["exact"]


def test_exact_outside_window_is_timing_with_its_deadline():
    s = Scenario()
    s.transfer("PAY-1", 12000, d(10), bank_on=d(16))
    outcome = s.run()
    assert outcome.matches == []
    exception = outcome.only_exception()
    assert exception["reason"] == "timing"
    assert "expected at the bank by 15 Sep 2026" in exception["explanation"]


def test_exact_bank_before_ledger_date_does_not_match():
    s = Scenario()
    s.transfer("PAY-1", 12000, d(10), bank_on=d(9))
    outcome = s.run()
    assert outcome.matches == [] and outcome.only_exception()["reason"] == "timing"


def test_exact_amount_one_cent_off_is_an_amount_mismatch():
    s = Scenario()
    entry, bank = s.transfer("PAY-1", 12000, d(10), bank_on=d(10), bank_amount=11999)
    outcome = s.run()
    assert outcome.matches == []
    exception = outcome.only_exception()
    assert (exception["reason"], exception["ledger"], exception["bank"]) == ("amount_mismatch", [entry], [bank])
    assert "difference EUR 0.01" in exception["explanation"]


def test_exact_different_reference_does_not_match():
    s = Scenario()
    s.ledger_row("PAY-1", 12000, d(10), channel="bank_transfer")
    s.bank_entry(12000, d(10), ustrd="PAY-2")
    outcome = s.run()
    assert outcome.matches == []
    assert sorted(e["reason"] for e in outcome.exceptions) == ["missing_from_bank", "unknown_deposit"]


def test_exact_bank_reference_is_compared_after_trimming_spaces():
    s = Scenario()
    s.ledger_row("PAY-1", 12000, d(10), channel="bank_transfer")
    s.bank_entry(12000, d(10), ustrd="  PAY-1 ")
    assert s.run().match_passes() == ["exact"]


def test_exact_takes_the_earliest_of_two_identical_credits_and_surfaces_the_other():
    s = Scenario()
    entry = s.ledger_row("PAY-1", 12000, d(10), channel="bank_transfer")
    later = s.bank_entry(12000, d(12), ustrd="PAY-1")
    earlier = s.bank_entry(12000, d(11), ustrd="PAY-1")
    outcome = s.run()
    assert outcome.matches[0]["bank"] == [earlier]
    exception = outcome.only_exception()
    assert (exception["reason"], exception["bank"]) == ("unknown_deposit", [later])


# --- pass B: gross_net --------------------------------------------------------------------------

def test_gross_net_matches_a_single_payment_payout():
    s = Scenario()
    ids = s.payout("ORR-PO-A", d(12), [("PAY-1", 10000)], created_on=d(10), bank_on=d(12))
    outcome = s.run()
    (match,) = outcome.matches
    assert (match["pass"], match["ledger"], match["settlement"], match["bank"]) == (
        "gross_net", ids["ledger"], ids["settlement"], ids["bank"])
    assert "= EUR 1.65, equal to the fee stated" in match["explanation"]


@pytest.mark.parametrize("delta, unexplained", [(-1, "EUR 0.01"), (1, "EUR -0.01"), (-40, "EUR 0.40")])
def test_gross_net_any_fee_difference_is_an_amount_mismatch(delta, unexplained):
    s = Scenario()
    ids = s.payout("ORR-PO-A", d(12), [("PAY-1", 10000)], created_on=d(10), bank_on=d(12),
                   bank_delta=delta)
    outcome = s.run()
    assert outcome.matches == []
    exception = outcome.only_exception()
    assert exception["reason"] == "amount_mismatch"
    assert (exception["ledger"], exception["settlement"], exception["bank"]) == (
        ids["ledger"], ids["settlement"], ids["bank"])
    assert f"unexplained {unexplained}" in exception["explanation"]


def test_gross_net_ledger_one_day_before_processor_date_still_matches():
    s = Scenario()
    s.ledger_row("PAY-1", 10000, d(9))
    s.payout("ORR-PO-A", d(12), [("PAY-1", 10000)], created_on=d(10), bank_on=d(12), ledger=False)
    assert s.run().match_passes() == ["gross_net"]


def test_gross_net_ledger_two_days_off_is_timing_not_a_match():
    s = Scenario()
    s.ledger_row("PAY-1", 10000, d(8))
    s.payout("ORR-PO-A", d(12), [("PAY-1", 10000)], created_on=d(10), bank_on=d(12), ledger=False)
    outcome = s.run()
    assert outcome.matches == []
    exception = outcome.only_exception()
    assert exception["reason"] == "timing" and "the processor recorded" in exception["explanation"]


@pytest.mark.parametrize("bank_day, matched", [(12, True), (15, True), (16, False), (11, False)])
def test_gross_net_payout_window(bank_day, matched):
    s = Scenario()
    s.payout("ORR-PO-A", d(12), [("PAY-1", 10000)], created_on=d(10), bank_on=d(bank_day))
    outcome = s.run()
    if matched:
        assert outcome.match_passes() == ["gross_net"]
    else:
        exception = outcome.only_exception()
        assert exception["reason"] == "timing"
        assert "outside the payout window (expected by 15 Sep 2026)" in exception["explanation"]


def test_gross_net_ledger_amount_different_from_gross_is_an_amount_mismatch():
    s = Scenario()
    s.ledger_row("PAY-1", 10001, d(10))
    s.payout("ORR-PO-A", d(12), [("PAY-1", 10000)], created_on=d(10), bank_on=d(12), ledger=False)
    exception = s.run().only_exception()
    assert exception["reason"] == "amount_mismatch"
    assert "EUR 100.01 vs settlement" in exception["explanation"]


def test_a_payout_credit_must_be_money_in():
    """A refund-only payout debited from the account cannot satisfy gross_net."""
    s = Scenario()
    s.ledger_row("PAY-1-R1", -2000, d(10))
    s.settlement_line("PAY-1-R1", -2000, "ORR-PO-R", d(12), created_on=d(10))
    s.bank_entry(-2000, d(12), end_to_end_id="ORR-PO-R", ustrd="ORRERY DEBIT")
    outcome = s.run()
    assert outcome.matches == []
    assert sorted(e["reason"] for e in outcome.exceptions) == ["missing_from_bank", "unexplained_debit"]


# --- pass C: many_to_one ------------------------------------------------------------------------

def test_many_to_one_matches_a_batch_including_a_refund():
    s = Scenario()
    s.ledger_row("PAY-9-R1", -1500, d(10))
    ids = s.payout("ORR-PO-B", d(12), [("PAY-1", 10000), ("PAY-2", 5000), ("PAY-3", 7000)],
                   created_on=d(10), bank_on=d(12), ledger=True, bank_delta=-1500)
    s.settlement_line("PAY-9-R1", -1500, "ORR-PO-B", d(12), created_on=d(10))
    outcome = s.run()
    (match,) = outcome.matches
    assert match["pass"] == "many_to_one" and len(match["ledger"]) == 4 and len(match["settlement"]) == 4
    assert match["bank"] == ids["bank"] and outcome.exceptions == []


def test_many_to_one_two_payments_is_a_batch():
    s = Scenario()
    s.payout("ORR-PO-B", d(12), [("PAY-1", 10000), ("PAY-2", 5000)], created_on=d(10), bank_on=d(12))
    assert s.run().match_passes() == ["many_to_one"]


def test_many_to_one_is_all_or_nothing_when_a_ledger_entry_is_missing():
    s = Scenario()
    s.ledger_row("PAY-1", 10000, d(10))
    s.ledger_row("PAY-2", 5000, d(10))
    ids = s.payout("ORR-PO-B", d(12), [("PAY-1", 10000), ("PAY-2", 5000), ("PAY-3", 7000)],
                   created_on=d(10), bank_on=d(12), ledger=False)
    outcome = s.run()
    assert outcome.matches == []
    exception = outcome.only_exception()
    assert exception["reason"] == "missing_from_ledger"
    assert exception["settlement"] == ids["settlement"] and len(exception["ledger"]) == 2
    assert f"settlement line {ids['settlement'][2]} (reference PAY-3" in exception["explanation"]


def test_many_to_one_deposit_one_cent_short_is_an_amount_mismatch():
    s = Scenario()
    s.payout("ORR-PO-B", d(12), [("PAY-1", 10000), ("PAY-2", 5000)], created_on=d(10),
             bank_on=d(12), bank_delta=-1)
    outcome = s.run()
    assert outcome.matches == []
    exception = outcome.only_exception()
    assert exception["reason"] == "amount_mismatch" and "unexplained EUR 0.01" in exception["explanation"]


def test_many_to_one_never_matches_without_the_bank_credit():
    s = Scenario()
    s.payout("ORR-PO-B", d(12), [("PAY-1", 10000), ("PAY-2", 5000)], created_on=d(10), bank_on=None)
    outcome = s.run()
    assert outcome.matches == [] and outcome.only_exception()["reason"] == "missing_from_bank"


# --- timing deadlines (D-048), hand-built -------------------------------------------------------

def test_d048_a_payout_dated_on_the_last_day_booked_after_the_period_is_timing():
    """(a) Dated 30 Sep, booked by the bank on 1 Oct: not in this statement, still in window."""
    s = Scenario()
    s.payout("ORR-PO-Z", d(30), [("PAY-1", 10000)], created_on=d(28), bank_on=None)
    exception = s.run().only_exception()
    assert exception["reason"] == "timing"
    assert "dated 30 Sep 2026" in exception["explanation"]
    assert "expected at the bank by 3 Oct 2026" in exception["explanation"]
    assert "If not booked by 3 Oct 2026, treat as missing." in exception["explanation"]


def test_d048_b_a_payout_inside_the_window_that_never_arrives_is_timing_with_its_deadline():
    """(b) Dated 28 Sep, never arrives: the window runs to 1 Oct, past the statement end."""
    s = Scenario()
    s.payout("ORR-PO-Y", d(28), [("PAY-1", 10000), ("PAY-2", 5000)], created_on=d(26), bank_on=None)
    exception = s.run().only_exception()
    assert exception["reason"] == "timing"
    assert "expected at the bank by 1 Oct 2026" in exception["explanation"]
    assert "treat as missing" in exception["explanation"]


def test_d048_boundary_window_closing_on_the_last_day_is_missing_not_timing():
    s = Scenario()
    s.payout("ORR-PO-X", d(27), [("PAY-1", 10000)], created_on=d(25), bank_on=None)
    exception = s.run().only_exception()
    assert exception["reason"] == "missing_from_bank"
    assert "window closed on 30 Sep 2026" in exception["explanation"]


@pytest.mark.parametrize("booked, reason, deadline", [(26, "timing", "1 Oct 2026"),
                                                      (25, "missing_from_bank", "30 Sep 2026")])
def test_d048_transfer_timing_uses_the_exact_window(booked, reason, deadline):
    s = Scenario()
    s.transfer("PAY-1", 12000, d(booked), bank_on=None)
    exception = s.run().only_exception()
    assert exception["reason"] == reason and deadline in exception["explanation"]


@pytest.mark.parametrize("booked, reason", [(30, "timing"), (29, "missing_from_bank")])
def test_d048_card_entry_without_settlement_uses_the_processor_window(booked, reason):
    s = Scenario()
    s.ledger_row("PAY-1", 10000, d(booked))
    exception = s.run().only_exception()
    assert exception["reason"] == reason and "processor's settlement report" in exception["explanation"]


def test_every_timing_explanation_states_a_deadline(demo):
    scenarios = []
    for build in (
        lambda s: s.payout("ORR-PO-Z", d(30), [("PAY-1", 10000)], created_on=d(28)),
        lambda s: s.transfer("PAY-1", 12000, d(10), bank_on=d(16)),
        lambda s: s.payout("ORR-PO-A", d(12), [("PAY-1", 10000)], created_on=d(10), bank_on=d(16)),
        lambda s: s.ledger_row("PAY-1", 10000, d(30)),
    ):
        s = Scenario()
        build(s)
        scenarios.append(s.run())
    explanations = [e["explanation"] for o in scenarios for e in o.exceptions if e["reason"] == "timing"]
    explanations += [e.explanation for e in demo.exceptions if e.reason == "timing"]
    assert len(explanations) == 7
    assert all(DEADLINE.search(text) for text in explanations), explanations


# --- duplicates ---------------------------------------------------------------------------------

def test_duplicate_card_entry_first_is_matched_second_is_flagged():
    s = Scenario()
    original = s.ledger_row("PAY-1", 10000, d(10))
    duplicate = s.ledger_row("PAY-1", 10000, d(10))
    s.payout("ORR-PO-A", d(12), [("PAY-1", 10000)], created_on=d(10), bank_on=d(12), ledger=False)
    outcome = s.run()
    assert outcome.matches[0]["ledger"] == [original]
    exception = outcome.only_exception()
    assert (exception["reason"], exception["ledger"]) == ("possible_duplicate", [duplicate])
    assert original in exception["explanation"]


def test_duplicate_bank_transfer_entry_is_flagged():
    s = Scenario()
    s.transfer("PAY-1", 12000, d(10), bank_on=d(10))
    duplicate = s.ledger_row("PAY-1", 12000, d(10), channel="bank_transfer")
    exception = s.run().only_exception()
    assert (exception["reason"], exception["ledger"]) == ("possible_duplicate", [duplicate])


def test_same_reference_different_amount_is_not_called_a_duplicate():
    s = Scenario()
    s.transfer("PAY-1", 12000, d(10), bank_on=d(10))
    s.ledger_row("PAY-1", 999, d(10), channel="bank_transfer")
    assert s.run().only_exception()["reason"] == "missing_from_bank"


def test_unmatched_twins_are_not_called_duplicates():
    """Neither copy reconciled: nothing proves which is the extra one."""
    s = Scenario()
    s.ledger_row("PAY-1", 12000, d(10), channel="bank_transfer")
    s.ledger_row("PAY-1", 12000, d(10), channel="bank_transfer")
    assert [e["reason"] for e in s.run().exceptions] == ["missing_from_bank", "missing_from_bank"]


# --- bank leftovers -----------------------------------------------------------------------------

def test_unknown_deposit_and_unexplained_debit():
    s = Scenario()
    deposit = s.bank_entry(25000, d(22), ustrd="SYNTHETIC UNREFERENCED")
    debit = s.bank_entry(-1200, d(23), ustrd="SYNTHETIC FEE")
    exceptions = s.run().exceptions
    assert [(e["reason"], e["bank"]) for e in exceptions] == [
        ("unknown_deposit", [deposit]), ("unexplained_debit", [debit])]


# --- input errors -------------------------------------------------------------------------------

def test_payout_reference_with_two_dates_is_refused():
    s = Scenario()
    s.payout("ORR-PO-A", d(12), [("PAY-1", 10000)], created_on=d(10), bank_on=d(12))
    s.settlement_line("PAY-2", 5000, "ORR-PO-A", d(13), created_on=d(10))
    s.ledger_row("PAY-2", 5000, d(10))
    with pytest.raises(EngineInputError, match="inconsistent"):
        s.run()


def test_parser_rejection_becomes_an_engine_input_error():
    ledger, settlement, bank = DEMO_FILES
    with pytest.raises(EngineInputError, match="Level 1 is EUR only"):
        run_engine(ledger.replace(b",EUR,", b",USD,", 1), settlement, bank)


# --- canonical output ---------------------------------------------------------------------------

def test_reconcile_is_byte_identical_on_repeat():
    assert reconcile(*DEMO_FILES) == reconcile(*DEMO_FILES)


def test_canonical_result_shape(demo):
    document = json.loads(demo.canonical())
    assert document["format"] == "recon-result/1" and document["engine_version"] == "1.0.0"
    assert [m["ordinal"] for m in document["matches"]] == list(range(1, 66))
    assert [e["ordinal"] for e in document["exceptions"]] == list(range(1, 8))
    assert all(m["ledger"] == sorted(m["ledger"]) for m in document["matches"])
    assert document["config"] == {"EXACT_WINDOW_DAYS": 5, "LEDGER_SETTLEMENT_WINDOW_DAYS": 1,
                                  "PAYOUT_WINDOW_DAYS": 3}


def test_canonical_json_refuses_floats():
    with pytest.raises(TypeError, match="float"):
        canonical_json({"amount": [1, {"x": 0.1}]})


def test_dates_in_explanations_do_not_depend_on_locale():
    from datetime import date
    assert _day(date(2026, 10, 1)) == "1 Oct 2026"


def test_fingerprints_are_distinct(demo):
    fingerprints = [e.fingerprint for e in demo.exceptions]
    assert len(set(fingerprints)) == len(fingerprints)
    assert all(re.fullmatch(r"[0-9a-f]{64}", f) for f in fingerprints)
