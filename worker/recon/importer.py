"""Import: store raw files idempotently, queue parse jobs, and process them.

Flow (identical for the web upload and the CLI):
1. ``store_file`` inserts the raw bytes through the database function ``upload_file()``, which
   the web upload calls too. The database computes the SHA-256 and its unique
   constraint makes the import idempotent: the same bytes a second time create nothing, not
   even a job, and the caller is told which file they already are.
2. A new file gets exactly one queued ``parse_file`` job.
3. ``process_parse_job`` parses the raw bytes and, in one transaction, writes the parse outcome,
   the rows (when parsed) and the job's completion. A file the parser rejects is a recorded
   outcome (``import_parse.status = 'rejected'`` with the reason), not a job failure (D-052).
   An unexpected error rolls the parse back and marks the job failed with the error.

All functions use ``conn.transaction()`` blocks, so they run as real transactions on an
autocommit connection and as savepoints inside a caller's transaction (tests).
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from recon import jobs
from recon.parse import PARSER_VERSION, ParseError
from recon.parse.bank import parse_bank
from recon.parse.csvfiles import parse_ledger, parse_settlement

FILE_KINDS = ("ledger", "settlement", "bank")

# Synthetic staff for the demo (D-031, D-054, D-073). Clearly not real people.
SYNTHETIC_STAFF = ("Demo Analyst 1", "Demo Analyst 2", "Demo Controller")
STAFF_ROLES = {"Demo Analyst 1": "analyst", "Demo Analyst 2": "analyst", "Demo Controller": "controller"}


@dataclass(frozen=True)
class StoredFile:
    file_id: int
    kind: str
    created: bool              # False: these exact bytes were already imported
    job_id: int | None         # the parse job queued for a new file


@dataclass(frozen=True)
class ParseOutcome:
    job_id: int
    file_id: int
    status: str                # 'parsed' or 'rejected'
    rows: int
    error: str | None


def seed_synthetic_staff(conn: psycopg.Connection) -> list[int]:
    with conn.transaction():
        for name in SYNTHETIC_STAFF:
            conn.execute(
                "INSERT INTO staff_user (display_name, is_synthetic, role) VALUES (%s, true, %s) "
                "ON CONFLICT (display_name) DO NOTHING",
                (name, STAFF_ROLES[name]),
            )
        return [row[0] for row in conn.execute(
            "SELECT id FROM staff_user WHERE display_name = ANY(%s) ORDER BY id",
            (list(SYNTHETIC_STAFF),),
        )]


def staff_id_by_name(conn: psycopg.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM staff_user WHERE display_name = %s", (name,)).fetchone()
    if row is None:
        raise LookupError(f"no staff user named {name!r}; run `python -m recon seed-staff` first")
    return row[0]


def store_file(conn: psycopg.Connection, kind: str, original_name: str, raw: bytes,
               staff_id: int) -> StoredFile:
    """Store the bytes and queue their parse job, requested by the uploader, through the
    database function upload_file(): the same one the web upload calls (D-091)."""
    if kind not in FILE_KINDS:
        raise ValueError(f"unknown file kind {kind!r}; expected one of {FILE_KINDS}")
    with conn.transaction():
        file_id, stored_kind, created, job_id = conn.execute(
            "SELECT stored_file_id, stored_kind, created, parse_job_id "
            "FROM upload_file(%s, %s, %s, %s)",
            (kind, original_name, raw, staff_id),
        ).fetchone()
        return StoredFile(file_id, str(stored_kind), created=created, job_id=job_id)


def _insert_rows(conn: psycopg.Connection, file_id: int, kind: str, raw: bytes) -> int:
    """Parse and insert. Raises ParseError if the file is rejected."""
    if kind == "ledger":
        records = parse_ledger(raw)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO ledger_entry (import_file_id, row_number, entry_id, booked_on, "
                "entry_type, channel, payment_reference, amount_minor, currency, customer_ref, "
                "description) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(file_id, r.row_number, r.entry_id, r.booked_on, r.entry_type, r.channel,
                  r.payment_reference, r.amount_minor, r.currency, r.customer_ref, r.description)
                 for r in records],
            )
        return len(records)
    if kind == "settlement":
        records = parse_settlement(raw)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO settlement_line (import_file_id, row_number, balance_transaction_id, "
                "created_on, line_type, merchant_reference, currency, gross_minor, fee_minor, "
                "net_minor, payout_id, payout_reference, payout_date) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(file_id, r.row_number, r.balance_transaction_id, r.created_on, r.line_type,
                  r.merchant_reference, r.currency, r.gross_minor, r.fee_minor, r.net_minor,
                  r.payout_id, r.payout_reference, r.payout_date)
                 for r in records],
            )
        return len(records)
    if kind == "bank":
        statement = parse_bank(raw)
        conn.execute(
            "INSERT INTO bank_statement (import_file_id, msg_id, stmt_id, account_id, currency, "
            "period_from, period_to, opening_minor, closing_minor) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (file_id, statement.msg_id, statement.stmt_id, statement.account_id,
             statement.currency, statement.period_from, statement.period_to,
             statement.opening_minor, statement.closing_minor),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO bank_entry (import_file_id, entry_index, amount_minor, cdt_dbt_ind, "
                "currency, status, booking_date, value_date, acct_svcr_ref, ntry_ref, "
                "bank_tx_code, end_to_end_id, remittance_ustrd, debtor_name) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(file_id, e.entry_index, e.amount_minor, e.cdt_dbt_ind, e.currency, e.status,
                  e.booking_date, e.value_date, e.acct_svcr_ref, e.ntry_ref, e.bank_tx_code,
                  e.end_to_end_id, e.remittance_ustrd, e.debtor_name)
                 for e in statement.entries],
            )
        return len(statement.entries)
    raise ValueError(f"unknown file kind {kind!r}")


def process_parse_job(conn: psycopg.Connection, job_id: int | None = None) -> ParseOutcome | None:
    """Process one queued parse job (the given one, or the oldest). None if nothing queued.

    The parse outcome, the rows and the job's completion commit together, and only while this
    worker still holds the job's lease (D-081): if the lease lapsed, LeaseLost rolls it all back.
    """
    claimed = jobs.claim(conn, "parse_file", "import_file_id", job_id)
    if claimed is None:
        return None
    job_id, (file_id,) = claimed.job_id, claimed.arguments
    try:
        with conn.transaction():
            kind, raw = conn.execute(
                "SELECT kind, raw FROM import_file WHERE id = %s", (file_id,)
            ).fetchone()
            kind, raw = str(kind), bytes(raw)
            try:
                with conn.transaction():
                    # Parse outcome first: rows reference (file, kind, 'parsed') (D-032).
                    conn.execute(
                        "INSERT INTO import_parse (import_file_id, kind, parser_version, status) "
                        "VALUES (%s, %s, %s, 'parsed')",
                        (file_id, kind, PARSER_VERSION),
                    )
                    rows = _insert_rows(conn, file_id, kind, raw)
                outcome = ParseOutcome(job_id, file_id, "parsed", rows, None)
            except ParseError as rejection:
                conn.execute(
                    "INSERT INTO import_parse (import_file_id, kind, parser_version, status, error) "
                    "VALUES (%s, %s, %s, 'rejected', %s)",
                    (file_id, kind, PARSER_VERSION, str(rejection)),
                )
                outcome = ParseOutcome(job_id, file_id, "rejected", 0, str(rejection))
            jobs.finish(conn, claimed, "done")
        return outcome
    except jobs.LeaseLost:
        raise
    except Exception as exc:
        jobs.fail_quietly(conn, claimed, f"{type(exc).__name__}: {exc}")
        raise


def run_parse_jobs(conn: psycopg.Connection) -> list[ParseOutcome]:
    """Process queued parse jobs until none remain. Reconcile and replay jobs are stage 4+."""
    outcomes = []
    while (outcome := process_parse_job(conn)) is not None:
        outcomes.append(outcome)
    return outcomes
