"""Builders for test rows. All values are synthetic.

``Builder`` inserts single rows with sensible defaults. ``Builder.standard_month`` builds a
small, correctly reconciled scenario that tests then break in one specific way each.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date

import psycopg


@dataclass
class Month:
    """Ids of a standard scenario. Keys are short labels used in the docstring below."""

    staff: int
    ledger_file: int
    settlement_file: int
    bank_file: int
    ledger: dict[str, int] = field(default_factory=dict)
    settlement: dict[str, int] = field(default_factory=dict)
    bank: dict[str, int] = field(default_factory=dict)


# One allocation plan entry: ("match", pass, ledger, settlement, bank)
#                         or ("exception", reason, ledger, settlement, bank)
# where ledger/settlement/bank are tuples of Month labels.
PlanEntry = tuple[str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]

# The correct allocation of ``standard_month``.
STANDARD_PLAN: list[PlanEntry] = [
    ("match", "exact", ("L1",), (), ("B1",)),
    ("match", "gross_net", ("L2",), ("S1",), ("B2",)),
    ("match", "many_to_one", ("L3", "L4"), ("S2", "S3"), ("B3",)),
    ("exception", "missing_from_bank", ("L5",), (), ()),
    ("exception", "unknown_deposit", (), (), ("B4",)),
]


class Builder:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self._seq = itertools.count(1)

    def _n(self) -> int:
        return next(self._seq)

    def _one(self, query: str, params: tuple) -> int:
        row = self.conn.execute(query, params).fetchone()
        assert row is not None
        return row[0]

    # --- staff and files -------------------------------------------------------------

    def staff(self, name: str | None = None) -> int:
        name = name or f"Synthetic Staff {self._n():04d}"
        return self._one(
            "INSERT INTO staff_user (display_name) VALUES (%s) RETURNING id", (name,)
        )

    def file(self, kind: str, raw: bytes | None = None, staff: int | None = None) -> int:
        n = self._n()
        raw = raw if raw is not None else f"SYNTHETIC DEMO DATA test file {kind} {n}\n".encode()
        staff = staff if staff is not None else self.staff()
        return self._one(
            "INSERT INTO import_file (kind, original_name, raw, uploaded_by) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (kind, f"synthetic-{kind}-{n}.dat", raw, staff),
        )

    def parse(self, file_id: int, kind: str, status: str = "parsed", error: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO import_parse (import_file_id, kind, parser_version, status, error) "
            "VALUES (%s, %s, '1.0.0', %s, %s)",
            (file_id, kind, status, error),
        )

    def parsed_file(self, kind: str, staff: int | None = None) -> int:
        file_id = self.file(kind, staff=staff)
        self.parse(file_id, kind)
        return file_id

    # --- input rows ------------------------------------------------------------------

    def ledger(
        self,
        file_id: int,
        reference: str,
        amount: int,
        *,
        channel: str = "card",
        booked_on: date = date(2026, 9, 1),
        currency: str = "EUR",
        entry_type: str | None = None,
        entry_id: str | None = None,
        row_number: int | None = None,
    ) -> int:
        n = self._n()
        return self._one(
            "INSERT INTO ledger_entry (import_file_id, row_number, entry_id, booked_on, entry_type, "
            "channel, payment_reference, amount_minor, currency, customer_ref, description) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                file_id,
                row_number or n,
                entry_id or f"LE-{n:06d}",
                booked_on,
                entry_type or ("payment" if amount > 0 else "refund"),
                channel,
                reference,
                amount,
                currency,
                "SYN-CUST-0001",
                "Synthetic order",
            ),
        )

    def settlement(
        self,
        file_id: int,
        reference: str,
        gross: int,
        fee: int,
        *,
        payout_reference: str = "ORR-PO-A",
        net: int | None = None,
        created_on: date = date(2026, 9, 1),
        payout_date: date = date(2026, 9, 3),
        currency: str = "EUR",
        line_type: str | None = None,
        row_number: int | None = None,
    ) -> int:
        n = self._n()
        return self._one(
            "INSERT INTO settlement_line (import_file_id, row_number, balance_transaction_id, "
            "created_on, line_type, merchant_reference, currency, gross_minor, fee_minor, net_minor, "
            "payout_id, payout_reference, payout_date) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                file_id,
                row_number or n,
                f"BT-{n:07d}",
                created_on,
                line_type or ("charge" if gross > 0 else "refund"),
                reference,
                currency,
                gross,
                fee,
                gross - fee if net is None else net,
                payout_reference.replace("ORR-", ""),
                payout_reference,
                payout_date,
            ),
        )

    def statement(self, file_id: int, opening: int = 0, closing: int = 0) -> None:
        self.conn.execute(
            "INSERT INTO bank_statement (import_file_id, msg_id, stmt_id, account_id, currency, "
            "period_from, period_to, opening_minor, closing_minor) "
            "VALUES (%s, 'SYN-MSG-1', 'SYN-STMT-1', 'SYNTHETIC-DEMO-ACCT-0001', 'EUR', "
            "'2026-09-01', '2026-09-30', %s, %s)",
            (file_id, opening, closing),
        )

    def bank(
        self,
        file_id: int,
        amount: int,
        *,
        end_to_end_id: str | None = None,
        ustrd: str | None = None,
        booking_date: date = date(2026, 9, 3),
        currency: str = "EUR",
        cdt_dbt_ind: str | None = None,
        status: str = "BOOK",
        entry_index: int | None = None,
    ) -> int:
        n = self._n()
        return self._one(
            "INSERT INTO bank_entry (import_file_id, entry_index, amount_minor, cdt_dbt_ind, currency, "
            "status, booking_date, value_date, acct_svcr_ref, bank_tx_code, end_to_end_id, "
            "remittance_ustrd, debtor_name) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'PMNT/RCDT/ESCT', %s, %s, %s) RETURNING id",
            (
                file_id,
                entry_index or n,
                amount,
                cdt_dbt_ind or ("CRDT" if amount > 0 else "DBIT"),
                currency,
                status,
                booking_date,
                booking_date,
                f"SYN-REF-{n:06d}",
                end_to_end_id,
                ustrd,
                "Synthetic Sender",
            ),
        )

    # --- runs ------------------------------------------------------------------------

    def run(self, ledger_file: int, settlement_file: int, bank_file: int, **extra) -> int:
        columns = ["engine_version", "ledger_file_id", "settlement_file_id", "bank_file_id"]
        values: list = ["1.0.0", ledger_file, settlement_file, bank_file]
        for key, value in extra.items():
            columns.append(key)
            values.append(value)
        placeholders = ", ".join(["%s"] * len(values))
        return self._one(
            f"INSERT INTO reconciliation_run ({', '.join(columns)}) VALUES ({placeholders}) RETURNING id",
            tuple(values),
        )

    def match(self, run_id: int, ordinal: int, pass_: str) -> int:
        return self._one(
            "INSERT INTO run_match (run_id, ordinal, pass, explanation) "
            "VALUES (%s, %s, %s, 'synthetic test match') RETURNING id",
            (run_id, ordinal, pass_),
        )

    def exception(self, run_id: int, ordinal: int, reason: str) -> int:
        return self._one(
            "INSERT INTO run_exception (run_id, ordinal, suggested_reason, explanation, fingerprint) "
            "VALUES (%s, %s, %s, 'synthetic test exception', sha256(%s::bytea)) RETURNING id",
            (run_id, ordinal, reason, f"{run_id}-{ordinal}".encode()),
        )

    def alloc_ledger(self, run_id: int, file_id: int, row_id: int, *, match: int | None = None,
                     exception: int | None = None) -> None:
        self.conn.execute(
            "INSERT INTO allocation_ledger (run_id, ledger_file_id, ledger_entry_id, match_id, exception_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (run_id, file_id, row_id, match, exception),
        )

    def alloc_settlement(self, run_id: int, file_id: int, row_id: int, *, match: int | None = None,
                         exception: int | None = None) -> None:
        self.conn.execute(
            "INSERT INTO allocation_settlement (run_id, settlement_file_id, settlement_line_id, match_id, "
            "exception_id) VALUES (%s, %s, %s, %s, %s)",
            (run_id, file_id, row_id, match, exception),
        )

    def alloc_bank(self, run_id: int, file_id: int, row_id: int, *, match: int | None = None,
                   exception: int | None = None) -> None:
        self.conn.execute(
            "INSERT INTO allocation_bank (run_id, bank_file_id, bank_entry_id, match_id, exception_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (run_id, file_id, row_id, match, exception),
        )

    def finish(self, run_id: int, result: bytes = b'{"synthetic":true}') -> None:
        self.conn.execute(
            "UPDATE reconciliation_run SET status = 'finished', finished_at = now(), "
            "result_canonical = %s WHERE id = %s",
            (result, run_id),
        )

    # --- scenarios -------------------------------------------------------------------

    def standard_month(self) -> Month:
        """A tiny, fully reconcilable synthetic month.

        L1 (bank transfer, EUR 120.00, ref PAY-1)  <->  B1 (credit EUR 120.00, Ustrd PAY-1)  exact
        L2 (card EUR 100.00, PAY-2) + S1 (gross 100.00, fee 1.65, net 98.35, payout ORR-PO-A)
            <->  B2 (credit EUR 98.35, EndToEndId ORR-PO-A)                               gross_net
        L3 (EUR 50.00, PAY-3), L4 (EUR 70.00, PAY-4) + S2, S3 in payout ORR-PO-B
            <->  B3 (credit 49.05 + 68.77 = EUR 117.82, EndToEndId ORR-PO-B)               many_to_one
        L5 (card EUR 30.00, PAY-5): nothing anywhere                         missing_from_bank
        B4 (credit EUR 250.00): nothing anywhere                             unknown_deposit
        """
        staff = self.staff()
        lf = self.parsed_file("ledger", staff)
        sf = self.parsed_file("settlement", staff)
        bf = self.parsed_file("bank", staff)
        self.statement(bf)
        m = Month(staff=staff, ledger_file=lf, settlement_file=sf, bank_file=bf)

        m.ledger["L1"] = self.ledger(lf, "PAY-1", 12000, channel="bank_transfer")
        m.ledger["L2"] = self.ledger(lf, "PAY-2", 10000)
        m.ledger["L3"] = self.ledger(lf, "PAY-3", 5000)
        m.ledger["L4"] = self.ledger(lf, "PAY-4", 7000)
        m.ledger["L5"] = self.ledger(lf, "PAY-5", 3000)

        m.settlement["S1"] = self.settlement(sf, "PAY-2", 10000, 165, payout_reference="ORR-PO-A")
        m.settlement["S2"] = self.settlement(sf, "PAY-3", 5000, 95, payout_reference="ORR-PO-B")
        m.settlement["S3"] = self.settlement(sf, "PAY-4", 7000, 123, payout_reference="ORR-PO-B")

        m.bank["B1"] = self.bank(bf, 12000, ustrd="PAY-1")
        m.bank["B2"] = self.bank(bf, 9835, end_to_end_id="ORR-PO-A")
        m.bank["B3"] = self.bank(bf, 4905 + 6877, end_to_end_id="ORR-PO-B")
        m.bank["B4"] = self.bank(bf, 25000, ustrd="SYNTHETIC UNKNOWN SENDER")
        return m

    def allocate(self, run_id: int, month: Month, plan: list[PlanEntry]) -> dict[int, int]:
        """Write matches, exceptions and allocations for ``plan``. Returns ordinal -> id."""
        ids: dict[int, int] = {}
        for ordinal, (what, label, ledger, settlement, bank) in enumerate(plan, start=1):
            if what == "match":
                target = {"match": self.match(run_id, ordinal, label)}
            else:
                target = {"exception": self.exception(run_id, ordinal, label)}
            ids[ordinal] = next(iter(target.values()))
            for key in ledger:
                self.alloc_ledger(run_id, month.ledger_file, month.ledger[key], **target)
            for key in settlement:
                self.alloc_settlement(run_id, month.settlement_file, month.settlement[key], **target)
            for key in bank:
                self.alloc_bank(run_id, month.bank_file, month.bank[key], **target)
        return ids

    def finished_standard_run(self) -> tuple[Month, int, dict[int, int]]:
        """Standard month, run, correct allocation, finished. Returns (month, run_id, ids)."""
        month = self.standard_month()
        run_id = self.run(month.ledger_file, month.settlement_file, month.bank_file)
        ids = self.allocate(run_id, month, STANDARD_PLAN)
        self.finish(run_id)
        return month, run_id, ids
