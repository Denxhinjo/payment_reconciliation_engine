"""The exceptions queue: list exceptions, show their evidence, resolve them, correct resolutions.

The database is the only authority on what a valid resolution is (design §4.5, D-019, D-020).
This module does not re-implement those rules. It inserts, lets the database refuse, and turns
each refusal into a specific, plain message. A refusal it does not recognise is re-raised
unchanged rather than mislabelled.

Resolving and correcting are both a single INSERT into the append-only ``resolution`` table:
- ``resolve``: the first resolution of an exception (``supersedes_id`` NULL).
- ``correct``: a new resolution that supersedes the one in force. The caller names the
  resolution being corrected; if someone else corrected it meanwhile, the database's
  "superseded at most once" rule refuses this one as stale (optimistic concurrency, D-066).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import psycopg

QUEUE_STATUSES = ("all", "open", "resolved")


class ResolutionRefused(Exception):
    """The database refused the resolution; the message says why, in plain words."""


class AlreadyResolved(ResolutionRefused):
    pass


class StaleCorrection(ResolutionRefused):
    pass


# --- reading ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class QueueItem:
    exception_id: int
    run_id: int
    ordinal: int
    suggested_reason: str
    explanation: str
    status: str
    current_resolution_id: int | None
    current_reason_code: str | None
    current_note: str | None
    resolved_by: str | None
    resolved_at: datetime | None
    resolution_count: int


@dataclass(frozen=True)
class ResolutionRecord:
    resolution_id: int
    supersedes_id: int | None
    reason_code: str
    reason_label: str
    note: str
    resolved_by: str
    created_at: datetime
    in_force: bool


@dataclass(frozen=True)
class EvidenceRow:
    source: str                # 'ledger', 'settlement' or 'bank'
    identifier: str            # entry_id, balance_transaction_id, or "#index AcctSvcrRef"
    on: date
    reference: str | None
    amount_minor: int
    detail: str


@dataclass(frozen=True)
class ExceptionDetail:
    item: QueueItem
    evidence: tuple[EvidenceRow, ...]
    history: tuple[ResolutionRecord, ...]   # oldest first; the last one is in force


_QUEUE_COLUMNS = (
    "exception_id, run_id, ordinal, suggested_reason, explanation, status, current_resolution_id, "
    "current_reason_code, current_note, resolved_by, resolved_at, resolution_count"
)


def queue(conn: psycopg.Connection, run_id: int, status: str = "all") -> list[QueueItem]:
    if status not in QUEUE_STATUSES:
        raise ValueError(f"status must be one of {QUEUE_STATUSES}")
    rows = conn.execute(
        f"SELECT {_QUEUE_COLUMNS} FROM exception_queue "
        "WHERE run_id = %s AND (%s = 'all' OR status = %s) ORDER BY ordinal",
        (run_id, status, status),
    ).fetchall()
    return [QueueItem(*row) for row in rows]


def queue_item(conn: psycopg.Connection, exception_id: int) -> QueueItem:
    row = conn.execute(
        f"SELECT {_QUEUE_COLUMNS} FROM exception_queue WHERE exception_id = %s", (exception_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"no exception with id {exception_id}")
    return QueueItem(*row)


def history(conn: psycopg.Connection, exception_id: int) -> list[ResolutionRecord]:
    """The resolution chain from the first resolution to the one in force."""
    rows = conn.execute(
        "WITH RECURSIVE chain AS ("
        "  SELECT r.*, 1 AS depth FROM resolution r "
        "   WHERE r.exception_id = %s AND r.supersedes_id IS NULL"
        "  UNION ALL"
        "  SELECT r.*, c.depth + 1 FROM resolution r JOIN chain c ON r.supersedes_id = c.id"
        ") "
        "SELECT c.id, c.supersedes_id, c.reason_code, rr.label, c.note, s.display_name, c.created_at, "
        "       NOT EXISTS (SELECT 1 FROM resolution n WHERE n.supersedes_id = c.id) "
        "  FROM chain c JOIN resolution_reason rr ON rr.code = c.reason_code "
        "  JOIN staff_user s ON s.id = c.resolved_by ORDER BY c.depth",
        (exception_id,),
    ).fetchall()
    return [ResolutionRecord(*row) for row in rows]


def evidence(conn: psycopg.Connection, exception_id: int) -> list[EvidenceRow]:
    """The input rows the exception cites, from all three sources."""
    rows = conn.execute(
        "SELECT 'ledger', le.entry_id, le.booked_on, le.payment_reference, le.amount_minor, "
        "       le.channel || ' ' || le.entry_type, 1, le.row_number "
        "  FROM allocation_ledger a JOIN ledger_entry le ON le.id = a.ledger_entry_id "
        " WHERE a.exception_id = %(e)s "
        "UNION ALL "
        "SELECT 'settlement', sl.balance_transaction_id, sl.created_on, sl.merchant_reference, "
        "       sl.gross_minor, 'fee ' || sl.fee_minor || ', net ' || sl.net_minor || ', payout ' "
        "       || sl.payout_id || ' dated ' || sl.payout_date, 2, sl.row_number "
        "  FROM allocation_settlement a JOIN settlement_line sl ON sl.id = a.settlement_line_id "
        " WHERE a.exception_id = %(e)s "
        "UNION ALL "
        "SELECT 'bank', '#' || be.entry_index || ' ' || coalesce(be.acct_svcr_ref, ''), "
        "       be.booking_date, coalesce(be.end_to_end_id, be.remittance_ustrd), be.amount_minor, "
        "       be.cdt_dbt_ind || ' ' || be.bank_tx_code, 3, be.entry_index "
        "  FROM allocation_bank a JOIN bank_entry be ON be.id = a.bank_entry_id "
        " WHERE a.exception_id = %(e)s "
        "ORDER BY 7, 8",
        {"e": exception_id},
    ).fetchall()
    return [EvidenceRow(*row[:6]) for row in rows]


def detail(conn: psycopg.Connection, exception_id: int) -> ExceptionDetail:
    return ExceptionDetail(
        item=queue_item(conn, exception_id),
        evidence=tuple(evidence(conn, exception_id)),
        history=tuple(history(conn, exception_id)),
    )


def reason_codes(conn: psycopg.Connection) -> list[tuple[str, str]]:
    return conn.execute("SELECT code, label FROM resolution_reason ORDER BY code").fetchall()


# --- writing ----------------------------------------------------------------------------------

def _insert(conn: psycopg.Connection, exception_id: int, supersedes_id: int | None,
            reason_code: str, note: str, staff_id: int) -> int:
    try:
        with conn.transaction():
            (resolution_id,) = conn.execute(
                "INSERT INTO resolution (exception_id, supersedes_id, reason_code, note, resolved_by) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (exception_id, supersedes_id, reason_code, note, staff_id),
            ).fetchone()
            return resolution_id
    except psycopg.Error as exc:
        raise _explain(conn, exc, exception_id, supersedes_id, reason_code) from exc


def resolve(conn: psycopg.Connection, exception_id: int, reason_code: str, note: str,
            staff_id: int) -> int:
    """Record the first resolution of an exception. Returns the resolution id."""
    return _insert(conn, exception_id, None, reason_code, note, staff_id)


def correct(conn: psycopg.Connection, exception_id: int, supersedes_id: int, reason_code: str,
            note: str, staff_id: int) -> int:
    """Record a new resolution that supersedes ``supersedes_id``, which must be the one in force."""
    if supersedes_id is None:
        raise ResolutionRefused("a correction must name the resolution it supersedes; "
                                "use resolve for an exception's first resolution")
    return _insert(conn, exception_id, supersedes_id, reason_code, note, staff_id)


def _explain(conn: psycopg.Connection, exc: psycopg.Error, exception_id: int,
             supersedes_id: int | None, reason_code: str) -> Exception:
    constraint = exc.diag.constraint_name
    sqlstate = exc.sqlstate

    if constraint == "resolution_one_root_per_exception":
        current = queue_item(conn, exception_id)
        return AlreadyResolved(
            f"exception {exception_id} is already resolved (resolution #{current.current_resolution_id} "
            f"by {current.resolved_by}). To change it, correct resolution "
            f"#{current.current_resolution_id}; the original stays on record."
        )
    if constraint == "resolution_superseded_once":
        current = queue_item(conn, exception_id)
        return StaleCorrection(
            f"resolution #{supersedes_id} has already been corrected; the resolution in force is "
            f"#{current.current_resolution_id} by {current.resolved_by}. Review it and correct that "
            "one instead."
        )
    if constraint == "resolution_note_check":
        return ResolutionRefused(
            "a written note of at least 10 characters (not counting surrounding spaces) is required"
        )
    if constraint == "resolution_reason_code_fkey":
        codes = ", ".join(code for code, _ in reason_codes(conn))
        return ResolutionRefused(f"unknown reason code {reason_code!r}; valid codes: {codes}")
    if constraint == "resolution_resolved_by_fkey":
        return ResolutionRefused("unknown staff user")
    if constraint == "resolution_exception_id_fkey":
        return ResolutionRefused(f"no exception with id {exception_id}")
    if constraint == "resolution_supersedes_id_exception_id_fkey":
        return ResolutionRefused(
            f"resolution #{supersedes_id} does not belong to exception {exception_id}"
        )
    if sqlstate == "RC004":
        # The finished-run trigger fires before the foreign key, so it also reports an exception
        # id that does not exist (as a run "missing"). Say which case this is.
        try:
            queue_item(conn, exception_id)
        except LookupError:
            return ResolutionRefused(f"no exception with id {exception_id}")
        return ResolutionRefused(
            f"exception {exception_id} belongs to a run that has not finished; only exceptions of "
            "finished runs can be resolved"
        )
    return exc
