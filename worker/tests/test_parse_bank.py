"""camt.053.001.02 parser: the official ISO sample reads correctly, the demo statement parses,
and every Level 1 rejection rule fires.

Rejection tests mutate the demo statement so that it still passes the official XSD (asserted
where it matters), proving the refusal comes from the Level 1 rule named in the message and
not from schema validation.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from recon import camt053
from recon.parse import ParseError
from recon.parse.bank import apply_level1_rules, parse_bank, read_camt053, to_minor

ROOT = Path(__file__).resolve().parents[2]
ISO_SAMPLE = (ROOT / "docs" / "sources" / "iso20022" / "camt.053.001.02.xml").read_bytes()
DEMO = (ROOT / "demo-data" / "2026-09" / "synthetic_bank_camt053_2026-09.xml").read_bytes()
HEAD, FIRST_NTRY = DEMO.split(b"<Ntry>", 1)


def first_entry(old: bytes, new: bytes) -> bytes:
    """Replace ``old`` once, inside the first Ntry only."""
    assert old in FIRST_NTRY
    return HEAD + b"<Ntry>" + FIRST_NTRY.replace(old, new, 1)


def schema_valid(document: bytes) -> bytes:
    camt053.validate(document)
    return document


# --- official ISO sample (S2) -----------------------------------------------------------------

def test_iso_sample_reads_with_the_expected_values():
    document = read_camt053(ISO_SAMPLE)
    assert document.msg_id == "AAAASESS-FP-STAT001"
    (stmt,) = document.statements
    assert stmt.account_id == "50000000054910000003"
    assert (stmt.period_from, stmt.period_to) == (date(2010, 10, 18), date(2010, 10, 18))
    assert [(b.code, b.amount_text, b.currency, b.credit_debit) for b in stmt.balances] == [
        ("OPBD", "500000", "SEK", "CRDT"), ("CLBD", "435678.50", "SEK", "CRDT")]
    assert [(e.amount_text, e.credit_debit, e.bank_tx_code, e.has_batch, e.tx_details_count,
             e.end_to_end_id, e.debtor_name, e.acct_svcr_ref) for e in stmt.entries] == [
        ("105678.50", "CRDT", "PAYM/0001/0005", False, 1, "MUELL/FINP/RA12345", "MUELLER",
         "AAAASESS-FP-CN_98765/01"),
        ("200000", "DBIT", "PAYM/0001/0003", True, 0, None, None, "AAAASESS-FP-ACCR-01"),
        ("30000", "CRDT", "TREA/0002/0000", False, 1, "AAAASS1085FINPSS", None,
         "AAAASESS-FP-CONF-FX"),
    ]


def test_iso_sample_booking_datetime_is_taken_as_stated():
    """BookgDt/DtTm 2010-10-18T13:15:00+01:00 is business date 2010-10-18 (D-026)."""
    (stmt,) = read_camt053(ISO_SAMPLE).statements
    assert {e.booking_date for e in stmt.entries} == {date(2010, 10, 18)}


def test_iso_sample_balances_tie_out_in_its_own_currency():
    (stmt,) = read_camt053(ISO_SAMPLE).statements
    sign = {"CRDT": 1, "DBIT": -1}
    opening, closing = (sign[b.credit_debit] * to_minor(b.amount_text, 2, b.code)
                        for b in stmt.balances)
    entries = sum(sign[e.credit_debit] * to_minor(e.amount_text, 2, "x") for e in stmt.entries)
    assert opening + entries == closing


def test_iso_sample_is_rejected_by_level1_for_its_currency():
    with pytest.raises(ParseError, match="SEK is not supported; Level 1 is EUR only"):
        apply_level1_rules(read_camt053(ISO_SAMPLE))


# --- demo statement ---------------------------------------------------------------------------

def test_demo_statement_parses_and_ties_out():
    statement = parse_bank(DEMO)
    assert (statement.msg_id, statement.stmt_id, statement.account_id) == (
        "SYN-CAMT053-202609", "SYN-STMT-202609", "SYNTHETIC-DEMO-ACCT-0001")
    assert (statement.period_from, statement.period_to) == (date(2026, 9, 1), date(2026, 9, 30))
    assert len(statement.entries) == 67
    assert [e.entry_index for e in statement.entries] == list(range(1, 68))
    assert statement.opening_minor + sum(e.amount_minor for e in statement.entries) == \
        statement.closing_minor
    assert {e.bank_tx_code for e in statement.entries} == {"PMNT/RCDT/ESCT"}


def test_demo_payout_and_transfer_references_are_read():
    entries = parse_bank(DEMO).entries
    payouts = [e for e in entries if e.end_to_end_id]
    transfers = [e for e in entries if not e.end_to_end_id]
    assert all(e.end_to_end_id.startswith("ORR-PO-") for e in payouts)
    assert len(payouts) == 27 and len(transfers) == 40
    assert all(e.remittance_ustrd for e in transfers)


def test_parsing_is_deterministic():
    assert parse_bank(DEMO) == parse_bank(DEMO)


def test_booking_datetime_with_offset_keeps_the_stated_date():
    """23:30 at -05:00 is the next day in UTC; the stated business date must be kept (D-026)."""
    doc = first_entry(b"<BookgDt>\n          <Dt>2026-09-01</Dt>\n        </BookgDt>",
                      b"<BookgDt><DtTm>2026-09-01T23:30:00-05:00</DtTm></BookgDt>")
    assert parse_bank(schema_valid(doc)).entries[0].booking_date == date(2026, 9, 1)


# --- amount conversion ------------------------------------------------------------------------

@pytest.mark.parametrize("text, minor", [("1625.94", 162594), ("1625.940", 162594), ("1625", 162500),
                                         ("+0.5", 50), (".05", 5), ("0.00", 0)])
def test_decimal_to_minor_units(text, minor):
    assert to_minor(text, 2, "t") == minor


@pytest.mark.parametrize("text", ["1625.945", "0.001", "1e3", "", "."])
def test_decimal_to_minor_units_refuses(text):
    with pytest.raises(ParseError):
        to_minor(text, 2, "t")


def test_trailing_zero_precision_is_accepted():
    doc = schema_valid(first_entry(b">1625.94<", b">1625.94000<"))
    assert parse_bank(doc).entries[0].amount_minor == 162594


# --- Level 1 rejections (each mutation still passes the official XSD) -------------------------

def _two_tx_details() -> bytes:
    block = re.search(rb"<TxDtls>.*?</TxDtls>", FIRST_NTRY, re.S).group(0)
    return first_entry(block, block + block)


def _two_statements() -> bytes:
    block = re.search(rb"<Stmt>.*</Stmt>", DEMO, re.S).group(0)
    return DEMO.replace(block, block + block)


LEVEL1_CASES = {
    "pending entry": (first_entry(b"<Sts>BOOK</Sts>", b"<Sts>PDNG</Sts>"), "Level 1 accepts booked"),
    "reversal": (first_entry(b"<Sts>BOOK</Sts>", b"<RvslInd>true</RvslInd><Sts>BOOK</Sts>"),
                 "reversal entries"),
    "batch booking": (first_entry(b"<NtryDtls>", b"<NtryDtls><Btch><NbOfTxs>2</NbOfTxs></Btch>"),
                      "batch-booked"),
    "two TxDtls": (_two_tx_details(), "2 TxDtls"),
    "charges": (first_entry(b"<NtryDtls>", b'<Chrgs><Amt Ccy="EUR">1.00</Amt></Chrgs><NtryDtls>'),
                "per-entry charges"),
    "two statements": (_two_statements(), "2 Stmt elements"),
    "entry not in EUR": (first_entry(b'Ccy="EUR"', b'Ccy="USD"'), "Ntry #1: currency USD"),
    "account not in EUR": (DEMO.replace(b"<Ccy>EUR</Ccy>", b"<Ccy>USD</Ccy>", 1), "account currency USD"),
    "balances not in EUR": (DEMO.replace(b'Ccy="EUR"', b'Ccy="USD"'), "Bal OPBD: currency USD"),
    "sub-cent precision": (first_entry(b">1625.94<", b">1625.945<"), "more precise"),
    "zero amount": (first_entry(b">1625.94<", b">0.00<"), "amount is zero"),
    "missing booking date": (first_entry(b"<BookgDt>\n          <Dt>2026-09-01</Dt>\n        </BookgDt>", b""),
                             "BookgDt is missing"),
    "missing period": (re.sub(rb"<FrToDt>.*?</FrToDt>", b"", DEMO, flags=re.S), "FrToDt is missing"),
    "balances do not tie out": (DEMO.replace(b">209442.41<", b">209442.42<"), "do not tie out"),
}


@pytest.mark.parametrize("case", sorted(LEVEL1_CASES))
def test_level1_rejection(case):
    document, message = LEVEL1_CASES[case]
    schema_valid(document)
    with pytest.raises(ParseError, match=message):
        parse_bank(document)


# --- documents that never reach the Level 1 rules ---------------------------------------------

@pytest.mark.parametrize("document, message", [
    (re.sub(rb"<Sts>BOOK</Sts>", b"", DEMO, count=1), "XSD validation"),
    (DEMO.replace(b"camt.053.001.02", b"camt.053.001.08"), "camt.053.001.08 is not supported"),
    (b"<?xml version='1.0'?><!DOCTYPE x [<!ENTITY a 'b'>]>" + DEMO.split(b"?>", 1)[1], "DTD"),
    (b"this is not XML", "not well-formed"),
    (b"<Document/>", "expected Document in"),
], ids=["fails-xsd", "other-camt053-version", "declares-dtd", "not-xml", "wrong-namespace"])
def test_unreadable_documents_are_rejected(document, message):
    with pytest.raises(ParseError, match=message):
        parse_bank(document)
