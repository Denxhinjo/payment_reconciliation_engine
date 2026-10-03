"""Ledger and settlement CSV parsers: the demo month parses; every rejection rule fires."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from recon.parse import ParseError
from recon.parse.csvfiles import (
    LEDGER_HEADER, SETTLEMENT_HEADER, parse_ledger, parse_settlement,
)

DEMO = Path(__file__).resolve().parents[2] / "demo-data" / "2026-09"
LEDGER = (DEMO / "synthetic_ledger_2026-09.csv").read_bytes()
SETTLEMENT = (DEMO / "synthetic_orrery_settlement_2026-09.csv").read_bytes()

GOOD_LEDGER_ROW = "LE-000001,2026-09-01,payment,card,PAY-000001,EUR,4999,SYN-CUST-0001,Synthetic order"
GOOD_SETTLEMENT_ROW = ("1,BT-0000001,2026-09-01,charge,PAY-000001,EUR,4999,95,4904,PO-20260901,"
                       "ORR-PO-20260901,2026-09-03")


def ledger_csv(*rows: str, header: str = ",".join(LEDGER_HEADER), banner: bool = True) -> bytes:
    lines = (["# SYNTHETIC DEMO DATA"] if banner else []) + [header, *rows]
    return ("\n".join(lines) + "\n").encode()


def settlement_csv(*rows: str, header: str = ",".join(SETTLEMENT_HEADER)) -> bytes:
    return ("\n".join(["# SYNTHETIC DEMO DATA", header, *rows]) + "\n").encode()


# --- demo month -------------------------------------------------------------------------------

def test_demo_ledger_parses_completely():
    records = parse_ledger(LEDGER)
    assert len(records) == 653
    assert [r.row_number for r in records] == list(range(1, 654))
    first = records[0]
    assert (first.entry_id, first.booked_on, first.channel, first.payment_reference,
            first.amount_minor) == ("LE-000001", date(2026, 9, 1), "bank_transfer", "PAY-000001", 162594)
    assert sum(1 for r in records if r.entry_type == "refund") == 6


def test_demo_settlement_parses_completely():
    records = parse_settlement(SETTLEMENT)
    assert len(records) == 612
    assert all(r.net_minor == r.gross_minor - r.fee_minor for r in records)
    assert len({r.payout_reference for r in records}) == 30


def test_parsing_is_deterministic():
    assert parse_ledger(LEDGER) == parse_ledger(LEDGER)
    assert parse_settlement(SETTLEMENT) == parse_settlement(SETTLEMENT)


def test_banner_is_optional_and_crlf_is_accepted():
    plain = ledger_csv(GOOD_LEDGER_ROW, banner=False)
    crlf = plain.replace(b"\n", b"\r\n")
    assert parse_ledger(plain) == parse_ledger(crlf) == parse_ledger(ledger_csv(GOOD_LEDGER_ROW))


def test_surrounding_whitespace_is_stripped():
    (record,) = parse_ledger(ledger_csv(GOOD_LEDGER_ROW.replace("PAY-000001", "  PAY-000001 ")))
    assert record.payment_reference == "PAY-000001"


# --- shared format rejections -------------------------------------------------------------------

@pytest.mark.parametrize("raw, message", [
    (b"\xef\xbb\xbf" + ledger_csv(GOOD_LEDGER_ROW), "byte-order mark"),
    (ledger_csv(GOOD_LEDGER_ROW).replace(b"Synthetic order", b"Synthetic \xff order"), "UTF-8"),
    (b"# SYNTHETIC DEMO DATA only\n", "no header"),
    (ledger_csv(), "no data rows"),
    (ledger_csv(GOOD_LEDGER_ROW, header=",".join(LEDGER_HEADER[::-1])), "header"),
    (ledger_csv(GOOD_LEDGER_ROW, header=",".join(LEDGER_HEADER) + ",extra"), "header"),
    (ledger_csv(GOOD_LEDGER_ROW + ",unexpected"), "expected 9 columns"),
    (ledger_csv(GOOD_LEDGER_ROW, ""), "expected 9 columns"),
    (ledger_csv(GOOD_LEDGER_ROW, "# a comment after the header is data"), "expected 9 columns"),
], ids=["bom", "invalid-utf8", "no-header", "no-rows", "reordered-header", "extra-header-column",
        "extra-field", "blank-line", "hash-line-after-header"])
def test_format_rejections(raw, message):
    with pytest.raises(ParseError, match=message):
        parse_ledger(raw)


# --- ledger rules -----------------------------------------------------------------------------

@pytest.mark.parametrize("change, message", [
    (("4999", "49.99"), "not an integer amount"),
    (("4999", "1e3"), "not an integer amount"),
    (("4999", "4 999"), "not an integer amount"),
    (("4999", "0"), "zero"),
    (("4999", "-4999"), "must be positive"),
    (("payment,", "refund,"), "must be negative"),
    (("payment,", "chargeback,"), "entry_type"),
    (("card", "cash"), "channel"),
    (("EUR", "USD"), "Level 1 is EUR only"),
    (("2026-09-01", "2026-9-1"), "YYYY-MM-DD"),
    (("2026-09-01", "20260901"), "YYYY-MM-DD"),
    (("2026-09-01", "2026-02-30"), "not a valid date"),
    (("LE-000001", ""), "entry_id is empty"),
    (("PAY-000001", ""), "payment_reference is empty"),
])
def test_ledger_rejections(change, message):
    old, new = change
    with pytest.raises(ParseError, match=message):
        parse_ledger(ledger_csv(GOOD_LEDGER_ROW.replace(old, new, 1)))


def test_ledger_repeated_entry_id_is_rejected_with_both_lines():
    with pytest.raises(ParseError, match="repeats line 3"):
        parse_ledger(ledger_csv(GOOD_LEDGER_ROW, GOOD_LEDGER_ROW))


def test_ledger_error_names_the_line():
    with pytest.raises(ParseError, match="ledger line 4"):
        parse_ledger(ledger_csv(GOOD_LEDGER_ROW, GOOD_LEDGER_ROW.replace("LE-000001", "LE-2").replace("4999", "x")))


# --- settlement rules -------------------------------------------------------------------------

def test_good_settlement_row_parses():
    (record,) = parse_settlement(settlement_csv(GOOD_SETTLEMENT_ROW))
    assert (record.gross_minor, record.fee_minor, record.net_minor) == (4999, 95, 4904)
    assert record.payout_date == date(2026, 9, 3)


@pytest.mark.parametrize("change, message", [
    (("4999,95,4904", "4999,95,4900"), "contradicts itself"),
    (("4999,95,4904", "4999,-5,5004"), "fee_minor is negative"),
    (("4999,95,4904", "0,0,0"), "gross_minor is zero"),
    (("charge,PAY", "refund,PAY"), "must have negative gross"),
    (("1,BT", "2,BT"), "report_version"),
    (("EUR", "GBP"), "Level 1 is EUR only"),
    (("ORR-PO-20260901,", ","), "payout_reference is empty"),
    (("2026-09-03", "03/09/2026"), "YYYY-MM-DD"),
])
def test_settlement_rejections(change, message):
    old, new = change
    with pytest.raises(ParseError, match=message):
        parse_settlement(settlement_csv(GOOD_SETTLEMENT_ROW.replace(old, new, 1)))


def test_settlement_repeated_transaction_id_is_rejected():
    with pytest.raises(ParseError, match="repeats"):
        parse_settlement(settlement_csv(GOOD_SETTLEMENT_ROW, GOOD_SETTLEMENT_ROW))


def test_settlement_refund_line_parses():
    (record,) = parse_settlement(settlement_csv(
        "1,BT-0000009,2026-09-05,refund,PAY-000001-R1,EUR,-2000,0,-2000,PO-20260905,"
        "ORR-PO-20260905,2026-09-07"))
    assert record.line_type == "refund" and record.net_minor == -2000


def test_ledger_file_is_not_accepted_as_a_settlement_report():
    with pytest.raises(ParseError, match="header"):
        parse_settlement(LEDGER)
