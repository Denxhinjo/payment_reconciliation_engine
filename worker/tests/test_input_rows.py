"""Parsed input rows: sign conventions, internal consistency, provenance from parsed files."""

from __future__ import annotations

from tests.conftest import (
    CHECK_VIOLATION, FOREIGN_KEY_VIOLATION, UNIQUE_VIOLATION, raises_sqlstate,
)


# --- provenance -------------------------------------------------------------------------

def test_rows_require_a_parsed_file_of_the_right_kind(conn, build):
    bank_file = build.parsed_file("bank")
    with raises_sqlstate(conn, FOREIGN_KEY_VIOLATION):
        build.ledger(bank_file, "PAY-1", 1000)


def test_rows_cannot_be_attached_to_a_rejected_file(conn, build):
    file_id = build.file("ledger")
    build.parse(file_id, "ledger", status="rejected", error="header mismatch")
    with raises_sqlstate(conn, FOREIGN_KEY_VIOLATION):
        build.ledger(file_id, "PAY-1", 1000)


def test_rows_cannot_be_attached_to_an_unparsed_file(conn, build):
    file_id = build.file("settlement")
    with raises_sqlstate(conn, FOREIGN_KEY_VIOLATION):
        build.settlement(file_id, "PAY-1", 1000, 39)


# --- ledger -----------------------------------------------------------------------------

def test_ledger_payment_must_be_positive(conn, build):
    lf = build.parsed_file("ledger")
    with raises_sqlstate(conn, CHECK_VIOLATION):
        build.ledger(lf, "PAY-1", -1000, entry_type="payment")


def test_ledger_refund_must_be_negative(conn, build):
    lf = build.parsed_file("ledger")
    with raises_sqlstate(conn, CHECK_VIOLATION):
        build.ledger(lf, "PAY-1-R1", 2000, entry_type="refund")


def test_ledger_amount_cannot_be_zero(conn, build):
    lf = build.parsed_file("ledger")
    with raises_sqlstate(conn, CHECK_VIOLATION):
        build.ledger(lf, "PAY-1", 0, entry_type="payment")


def test_currency_must_be_three_uppercase_letters(conn, build):
    lf = build.parsed_file("ledger")
    with raises_sqlstate(conn, CHECK_VIOLATION):
        build.ledger(lf, "PAY-1", 1000, currency="eur")


def test_ledger_entry_id_unique_within_a_file(conn, build):
    lf = build.parsed_file("ledger")
    build.ledger(lf, "PAY-1", 1000, entry_id="LE-000001")
    with raises_sqlstate(conn, UNIQUE_VIOLATION):
        build.ledger(lf, "PAY-1", 1000, entry_id="LE-000001")


def test_planted_duplicate_shape_is_accepted(conn, build):
    """Two different entry ids for the same payment must reach the engine (planted P4)."""
    lf = build.parsed_file("ledger")
    build.ledger(lf, "PAY-7", 4999, entry_id="LE-000007")
    build.ledger(lf, "PAY-7", 4999, entry_id="LE-000008")


def test_row_number_unique_within_a_file(conn, build):
    lf = build.parsed_file("ledger")
    build.ledger(lf, "PAY-1", 1000, row_number=1)
    with raises_sqlstate(conn, UNIQUE_VIOLATION):
        build.ledger(lf, "PAY-2", 1000, row_number=1)


# --- settlement -------------------------------------------------------------------------

def test_settlement_net_must_equal_gross_minus_fee(conn, build):
    sf = build.parsed_file("settlement")
    with raises_sqlstate(conn, CHECK_VIOLATION):
        build.settlement(sf, "PAY-1", 10000, 165, net=9800)


def test_settlement_fee_cannot_be_negative(conn, build):
    sf = build.parsed_file("settlement")
    with raises_sqlstate(conn, CHECK_VIOLATION):
        build.settlement(sf, "PAY-1", 10000, -5)


def test_settlement_charge_must_be_positive(conn, build):
    sf = build.parsed_file("settlement")
    with raises_sqlstate(conn, CHECK_VIOLATION):
        build.settlement(sf, "PAY-1", -10000, 0, line_type="charge")


def test_settlement_refund_line_is_accepted(conn, build):
    sf = build.parsed_file("settlement")
    build.settlement(sf, "PAY-1-R1", -2000, 0)


def test_settlement_transaction_id_unique_within_a_file(conn, build):
    sf = build.parsed_file("settlement")
    insert = (
        "INSERT INTO settlement_line (import_file_id, row_number, balance_transaction_id, "
        "created_on, line_type, merchant_reference, currency, gross_minor, fee_minor, net_minor, "
        "payout_id, payout_reference, payout_date) VALUES "
        "(%s, %s, 'BT-1', '2026-09-01', 'charge', %s, 'EUR', 1000, 39, 961, 'PO-1', "
        "'ORR-PO-1', '2026-09-03')"
    )
    conn.execute(insert, (sf, 1, "PAY-1"))
    with raises_sqlstate(conn, UNIQUE_VIOLATION):
        conn.execute(insert, (sf, 2, "PAY-2"))


# --- bank -------------------------------------------------------------------------------

def test_bank_entry_requires_a_statement_header(conn, build):
    bf = build.parsed_file("bank")
    with raises_sqlstate(conn, FOREIGN_KEY_VIOLATION):
        build.bank(bf, 1000)


def test_bank_statement_requires_a_bank_file(conn, build):
    lf = build.parsed_file("ledger")
    with raises_sqlstate(conn, FOREIGN_KEY_VIOLATION):
        build.statement(lf)


def test_bank_credit_must_be_positive(conn, build):
    bf = build.parsed_file("bank")
    build.statement(bf)
    with raises_sqlstate(conn, CHECK_VIOLATION):
        build.bank(bf, -1000, cdt_dbt_ind="CRDT")


def test_bank_debit_must_be_negative(conn, build):
    bf = build.parsed_file("bank")
    build.statement(bf)
    with raises_sqlstate(conn, CHECK_VIOLATION):
        build.bank(bf, 1000, cdt_dbt_ind="DBIT")


def test_only_booked_bank_entries_are_stored(conn, build):
    bf = build.parsed_file("bank")
    build.statement(bf)
    with raises_sqlstate(conn, CHECK_VIOLATION):
        build.bank(bf, 1000, status="PDNG")


def test_statement_period_cannot_end_before_it_starts(conn, build):
    bf = build.parsed_file("bank")
    with raises_sqlstate(conn, CHECK_VIOLATION):
        conn.execute(
            "INSERT INTO bank_statement (import_file_id, msg_id, stmt_id, account_id, currency, "
            "period_from, period_to, opening_minor, closing_minor) "
            "VALUES (%s, 'M', 'S', 'SYNTHETIC-DEMO-ACCT-0001', 'EUR', '2026-09-30', '2026-09-01', 0, 0)",
            (bf,),
        )
