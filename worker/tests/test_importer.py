"""Import: idempotent storage, parse jobs, rows identical to the parser's output, rejections
recorded as outcomes, failures recorded on the job."""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import psycopg
import pytest

from recon import importer
from recon.migrate import migrate
from recon.parse import PARSER_VERSION
from recon.parse.bank import parse_bank
from recon.parse.csvfiles import parse_ledger, parse_settlement
from tests.conftest import fresh_database

DEMO = Path(__file__).resolve().parents[2] / "demo-data" / "2026-09"
FILES = {
    "ledger": DEMO / "synthetic_ledger_2026-09.csv",
    "settlement": DEMO / "synthetic_orrery_settlement_2026-09.csv",
    "bank": DEMO / "synthetic_bank_camt053_2026-09.xml",
}


@pytest.fixture
def staff(conn):
    return importer.seed_synthetic_staff(conn)[0]


def _store(conn, staff, kind, raw=None):
    raw = FILES[kind].read_bytes() if raw is None else raw
    return importer.store_file(conn, kind, f"synthetic-{kind}", raw, staff)


def _count(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


# --- harness guard ----------------------------------------------------------------------------

def test_importer_writes_stay_inside_the_test_transaction(conn, staff):
    """Guards D-056: the importer's transaction blocks must nest as savepoints here, never
    commit, or tests would leak rows into one another."""
    _store(conn, staff, "ledger")
    importer.run_parse_jobs(conn)
    assert conn.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS


# --- storage and idempotency ------------------------------------------------------------------

def test_new_file_is_stored_with_one_queued_parse_job(conn, staff):
    stored = _store(conn, staff, "ledger")
    assert stored.created and stored.job_id is not None
    assert conn.execute("SELECT kind, status FROM job WHERE id = %s", (stored.job_id,)).fetchone() == (
        "parse_file", "queued")


def test_raw_bytes_are_stored_unmodified(conn, staff):
    raw = FILES["ledger"].read_bytes().replace(b"\n", b"\r\n")   # CRLF must survive untouched
    stored = _store(conn, staff, "ledger", raw)
    stored_raw, digest = conn.execute(
        "SELECT raw, sha256 FROM import_file WHERE id = %s", (stored.file_id,)).fetchone()
    assert bytes(stored_raw) == raw
    assert bytes(digest) == hashlib.sha256(raw).digest()


def test_reimporting_the_same_bytes_does_nothing(conn, staff):
    first = _store(conn, staff, "settlement")
    importer.run_parse_jobs(conn)
    counts = [_count(conn, f"SELECT count(*) FROM {t}")
              for t in ("import_file", "job", "import_parse", "settlement_line")]
    again = _store(conn, staff, "settlement")
    assert (again.created, again.file_id, again.job_id) == (False, first.file_id, None)
    assert importer.run_parse_jobs(conn) == []
    assert counts == [_count(conn, f"SELECT count(*) FROM {t}")
                      for t in ("import_file", "job", "import_parse", "settlement_line")]


def test_same_bytes_uploaded_as_another_kind_reports_the_original(conn, staff):
    first = _store(conn, staff, "ledger")
    again = _store(conn, staff, "bank", FILES["ledger"].read_bytes())
    assert (again.created, again.file_id, again.kind) == (False, first.file_id, "ledger")


def test_unknown_kind_is_refused_before_touching_the_database(conn, staff):
    with pytest.raises(ValueError, match="unknown file kind"):
        importer.store_file(conn, "receipt", "x", b"data", staff)


# --- parsing into rows ------------------------------------------------------------------------

def test_demo_month_imports_completely(conn, staff):
    for kind in FILES:
        _store(conn, staff, kind)
    outcomes = importer.run_parse_jobs(conn)
    assert [(o.status, o.rows) for o in outcomes] == [("parsed", 653), ("parsed", 612), ("parsed", 67)]
    assert conn.execute(
        "SELECT DISTINCT parser_version, status FROM import_parse").fetchall() == [(PARSER_VERSION, "parsed")]
    assert _count(conn, "SELECT count(*) FROM job WHERE status = 'done'") == 3


def test_ledger_rows_equal_the_parser_output_field_for_field(conn, staff):
    stored = _store(conn, staff, "ledger")
    importer.process_parse_job(conn, stored.job_id)
    expected = [dataclasses.astuple(r) for r in parse_ledger(FILES["ledger"].read_bytes())]
    actual = conn.execute(
        "SELECT row_number, entry_id, booked_on, entry_type, channel, payment_reference, "
        "currency, amount_minor, customer_ref, description FROM ledger_entry "
        "WHERE import_file_id = %s ORDER BY row_number", (stored.file_id,)).fetchall()
    assert actual == expected


def test_settlement_rows_equal_the_parser_output_field_for_field(conn, staff):
    stored = _store(conn, staff, "settlement")
    importer.process_parse_job(conn, stored.job_id)
    expected = [dataclasses.astuple(r) for r in parse_settlement(FILES["settlement"].read_bytes())]
    actual = conn.execute(
        "SELECT row_number, balance_transaction_id, created_on, line_type, merchant_reference, "
        "currency, gross_minor, fee_minor, net_minor, payout_id, payout_reference, payout_date "
        "FROM settlement_line WHERE import_file_id = %s ORDER BY row_number",
        (stored.file_id,)).fetchall()
    assert actual == expected


def test_bank_rows_equal_the_parser_output_field_for_field(conn, staff):
    stored = _store(conn, staff, "bank")
    importer.process_parse_job(conn, stored.job_id)
    statement = parse_bank(FILES["bank"].read_bytes())
    header = conn.execute(
        "SELECT msg_id, stmt_id, account_id, currency, period_from, period_to, opening_minor, "
        "closing_minor FROM bank_statement WHERE import_file_id = %s", (stored.file_id,)).fetchone()
    assert header == (statement.msg_id, statement.stmt_id, statement.account_id, statement.currency,
                      statement.period_from, statement.period_to, statement.opening_minor,
                      statement.closing_minor)
    actual = conn.execute(
        "SELECT entry_index, amount_minor, cdt_dbt_ind, currency, status, booking_date, value_date, "
        "acct_svcr_ref, ntry_ref, bank_tx_code, end_to_end_id, remittance_ustrd, debtor_name "
        "FROM bank_entry WHERE import_file_id = %s ORDER BY entry_index", (stored.file_id,)).fetchall()
    assert actual == [dataclasses.astuple(e) for e in statement.entries]


# --- rejection and failure --------------------------------------------------------------------

def test_rejected_file_is_a_recorded_outcome_with_no_rows(conn, staff):
    bad = FILES["ledger"].read_bytes().replace(b",EUR,", b",USD,", 1)
    stored = _store(conn, staff, "ledger", bad)
    outcome = importer.process_parse_job(conn, stored.job_id)
    assert outcome.status == "rejected" and "Level 1 is EUR only" in outcome.error
    assert conn.execute(
        "SELECT status, error IS NOT NULL FROM import_parse WHERE import_file_id = %s",
        (stored.file_id,)).fetchone() == ("rejected", True)
    assert _count(conn, "SELECT count(*) FROM ledger_entry WHERE import_file_id = %s", stored.file_id) == 0
    assert conn.execute("SELECT status FROM job WHERE id = %s", (stored.job_id,)).fetchone() == ("done",)
    # The raw file is kept: the evidence of what was rejected stays.
    assert _count(conn, "SELECT count(*) FROM import_file WHERE id = %s", stored.file_id) == 1


def test_rejected_bank_file_leaves_no_statement_or_entries(conn, staff):
    bad = FILES["bank"].read_bytes().replace(b"<Sts>BOOK</Sts>", b"<Sts>PDNG</Sts>", 1)
    stored = _store(conn, staff, "bank", bad)
    assert importer.process_parse_job(conn, stored.job_id).status == "rejected"
    assert _count(conn, "SELECT count(*) FROM bank_statement WHERE import_file_id = %s", stored.file_id) == 0
    assert _count(conn, "SELECT count(*) FROM bank_entry WHERE import_file_id = %s", stored.file_id) == 0


def test_rejected_file_cannot_be_used_in_a_run(conn, staff, build):
    bad = _store(conn, staff, "bank", b"not a camt.053 file")
    importer.process_parse_job(conn, bad.job_id)
    good = {k: _store(conn, staff, k) for k in ("ledger", "settlement")}
    importer.run_parse_jobs(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with conn.transaction():
            build.run(good["ledger"].file_id, good["settlement"].file_id, bad.file_id)


def test_unexpected_error_marks_the_job_failed_and_leaves_no_partial_parse(conn, staff, monkeypatch):
    stored = _store(conn, staff, "ledger")

    def explode(*args):
        raise RuntimeError("synthetic infrastructure failure")

    monkeypatch.setattr(importer, "_insert_rows", explode)
    with pytest.raises(RuntimeError):
        importer.process_parse_job(conn, stored.job_id)
    status, attempts, error = conn.execute(
        "SELECT status, attempts, error FROM job WHERE id = %s", (stored.job_id,)).fetchone()
    assert (status, attempts) == ("failed", 1) and "synthetic infrastructure failure" in error
    assert _count(conn, "SELECT count(*) FROM import_parse WHERE import_file_id = %s", stored.file_id) == 0


def test_a_job_is_processed_once(conn, staff):
    stored = _store(conn, staff, "ledger")
    assert importer.process_parse_job(conn, stored.job_id) is not None
    assert importer.process_parse_job(conn, stored.job_id) is None


def test_worker_processes_jobs_queued_by_the_web_layer(conn, staff):
    """The web inserts import_file + job directly; the worker must pick them up."""
    (file_id,) = conn.execute(
        "INSERT INTO import_file (kind, original_name, raw, uploaded_by) "
        "VALUES ('settlement', 'web-upload.csv', %s, %s) RETURNING id",
        (FILES["settlement"].read_bytes(), staff)).fetchone()
    conn.execute("INSERT INTO job (kind, import_file_id, requested_by) VALUES ('parse_file', %s, %s)",
                 (file_id, staff))
    (outcome,) = importer.run_parse_jobs(conn)
    assert (outcome.file_id, outcome.status, outcome.rows) == (file_id, "parsed", 612)


def test_staff_lookup_fails_clearly_for_unknown_names(conn):
    with pytest.raises(LookupError, match="seed-staff"):
        importer.staff_id_by_name(conn, "Nobody")


def test_synthetic_staff_seeding_is_idempotent(conn):
    first = importer.seed_synthetic_staff(conn)
    assert importer.seed_synthetic_staff(conn) == first
    assert conn.execute(
        "SELECT bool_and(is_synthetic) FROM staff_user WHERE id = ANY(%s)", (first,)).fetchone() == (True,)


# --- command line, end to end on a fresh database ----------------------------------------------

def test_cli_seed_import_reimport_and_worker(capsys):
    from recon.__main__ import main

    with fresh_database() as url:
        migrate(url)
        assert main(["seed-staff", "--database-url", url]) == 0
        for kind, path in FILES.items():
            assert main(["import", "--database-url", url, "--kind", kind, "--file", str(path)]) == 0
        out = capsys.readouterr().out
        assert "653 rows" in out and "612 rows" in out and "67 rows" in out

        assert main(["import", "--database-url", url, "--kind", "ledger",
                     "--file", str(FILES["ledger"])]) == 0
        assert "already imported as file #1 (ledger); nothing done" in capsys.readouterr().out

        assert main(["worker", "--database-url", url]) == 0
        assert "0 parse job(s), 0 reconcile job(s) and 0 replay job(s) processed" in capsys.readouterr().out

        with psycopg.connect(url) as conn:
            assert conn.execute("SELECT count(*) FROM import_file").fetchone() == (3,)
            assert conn.execute("SELECT count(*) FROM ledger_entry").fetchone() == (653,)


def test_cli_reports_a_rejected_file_with_a_non_zero_exit(tmp_path, capsys):
    from recon.__main__ import main

    bad = tmp_path / "bad.csv"
    bad.write_bytes(b"# SYNTHETIC DEMO DATA\nnot,the,right,header\n")
    with fresh_database() as url:
        migrate(url)
        main(["seed-staff", "--database-url", url])
        assert main(["import", "--database-url", url, "--kind", "ledger", "--file", str(bad)]) == 1
        assert "REJECTED" in capsys.readouterr().err
