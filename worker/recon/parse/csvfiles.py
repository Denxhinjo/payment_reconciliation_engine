"""Parsers for the two CSV inputs: the internal ledger export and the Orrery settlement report.

Formats are defined in docs/design.md §7.1 and §7.2. Rules applied to both:
- UTF-8 without a byte-order mark; invalid UTF-8 rejects the file.
- Leading lines starting with '#' are skipped (the SYNTHETIC DEMO DATA banner); a '#' line
  anywhere else is data and will fail validation.
- The header must match exactly; every row must have exactly the header's columns.
- Surrounding whitespace is stripped from every field (D-055); nothing else is normalised.
- Amounts are integer minor units; Level 1 accepts EUR only (D-050).
- A file with no data rows is rejected.
Row numbers are 1-based positions among data rows (the natural key the engine cites).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date

from recon.parse import LEVEL1_CURRENCY, ParseError, strict_date, strict_int

LEDGER_HEADER = (
    "entry_id", "booked_on", "entry_type", "channel", "payment_reference", "currency",
    "amount_minor", "customer_ref", "description",
)
SETTLEMENT_HEADER = (
    "report_version", "balance_transaction_id", "created_on", "line_type", "merchant_reference",
    "currency", "gross_minor", "fee_minor", "net_minor", "payout_id", "payout_reference",
    "payout_date",
)
SETTLEMENT_REPORT_VERSION = "1"


@dataclass(frozen=True)
class LedgerRecord:
    row_number: int
    entry_id: str
    booked_on: date
    entry_type: str
    channel: str
    payment_reference: str
    currency: str
    amount_minor: int
    customer_ref: str
    description: str


@dataclass(frozen=True)
class SettlementRecord:
    row_number: int
    balance_transaction_id: str
    created_on: date
    line_type: str
    merchant_reference: str
    currency: str
    gross_minor: int
    fee_minor: int
    net_minor: int
    payout_id: str
    payout_reference: str
    payout_date: date


def _rows(raw: bytes, header: tuple[str, ...], what: str):
    """Yield (row_number, line_number, fields) for each data row, after format checks."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ParseError(f"{what}: file starts with a UTF-8 byte-order mark, which is not accepted")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParseError(f"{what}: not valid UTF-8 (byte offset {exc.start})") from exc

    lines = text.splitlines(keepends=True)
    banner = 0
    while banner < len(lines) and lines[banner].startswith("#"):
        banner += 1
    reader = csv.reader(io.StringIO("".join(lines[banner:])), strict=True)

    try:
        found_header = next(reader, None)
    except csv.Error as exc:
        raise ParseError(f"{what} line {banner + 1}: malformed CSV: {exc}") from exc
    if found_header is None:
        raise ParseError(f"{what}: no header row")
    if tuple(found_header) != header:
        raise ParseError(
            f"{what} line {banner + 1}: header {found_header!r} does not match the expected "
            f"{list(header)!r}"
        )

    row_number = 0
    while True:
        try:
            fields = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            raise ParseError(f"{what} line {banner + reader.line_num}: malformed CSV: {exc}") from exc
        line = banner + reader.line_num
        if len(fields) != len(header):
            raise ParseError(
                f"{what} line {line}: expected {len(header)} columns, found {len(fields)}"
            )
        row_number += 1
        yield row_number, line, dict(zip(header, (f.strip() for f in fields)))

    if row_number == 0:
        raise ParseError(f"{what}: no data rows")


def _required(fields: dict[str, str], name: str, where: str) -> str:
    if fields[name] == "":
        raise ParseError(f"{where}: {name} is empty")
    return fields[name]


def _one_of(fields: dict[str, str], name: str, allowed: tuple[str, ...], where: str) -> str:
    if fields[name] not in allowed:
        raise ParseError(f"{where}: {name} {fields[name]!r} is not one of {list(allowed)}")
    return fields[name]


def _level1_currency(fields: dict[str, str], where: str) -> str:
    if fields["currency"] != LEVEL1_CURRENCY:
        raise ParseError(
            f"{where}: currency {fields['currency']!r} is not supported; Level 1 is "
            f"{LEVEL1_CURRENCY} only"
        )
    return fields["currency"]


def parse_ledger(raw: bytes) -> list[LedgerRecord]:
    records: list[LedgerRecord] = []
    seen: dict[str, int] = {}
    for row_number, line, f in _rows(raw, LEDGER_HEADER, "ledger"):
        where = f"ledger line {line}"
        entry_id = _required(f, "entry_id", where)
        if entry_id in seen:
            raise ParseError(f"{where}: entry_id {entry_id!r} repeats line {seen[entry_id]}")
        seen[entry_id] = line
        entry_type = _one_of(f, "entry_type", ("payment", "refund"), where)
        amount = strict_int(f["amount_minor"], f"{where} amount_minor")
        if amount == 0:
            raise ParseError(f"{where}: amount_minor is zero")
        if (entry_type == "payment") != (amount > 0):
            raise ParseError(
                f"{where}: a {entry_type} must be {'positive' if entry_type == 'payment' else 'negative'}"
                f" (sign convention: positive = money in), found {amount}"
            )
        records.append(LedgerRecord(
            row_number=row_number,
            entry_id=entry_id,
            booked_on=strict_date(f["booked_on"], f"{where} booked_on"),
            entry_type=entry_type,
            channel=_one_of(f, "channel", ("card", "bank_transfer"), where),
            payment_reference=_required(f, "payment_reference", where),
            currency=_level1_currency(f, where),
            amount_minor=amount,
            customer_ref=f["customer_ref"],
            description=f["description"],
        ))
    return records


def parse_settlement(raw: bytes) -> list[SettlementRecord]:
    records: list[SettlementRecord] = []
    seen: dict[str, int] = {}
    for row_number, line, f in _rows(raw, SETTLEMENT_HEADER, "settlement"):
        where = f"settlement line {line}"
        if f["report_version"] != SETTLEMENT_REPORT_VERSION:
            raise ParseError(
                f"{where}: report_version {f['report_version']!r} is not supported "
                f"(expected {SETTLEMENT_REPORT_VERSION!r})"
            )
        transaction_id = _required(f, "balance_transaction_id", where)
        if transaction_id in seen:
            raise ParseError(
                f"{where}: balance_transaction_id {transaction_id!r} repeats line {seen[transaction_id]}"
            )
        seen[transaction_id] = line
        line_type = _one_of(f, "line_type", ("charge", "refund"), where)
        gross = strict_int(f["gross_minor"], f"{where} gross_minor")
        fee = strict_int(f["fee_minor"], f"{where} fee_minor")
        net = strict_int(f["net_minor"], f"{where} net_minor")
        if gross == 0:
            raise ParseError(f"{where}: gross_minor is zero")
        if fee < 0:
            raise ParseError(f"{where}: fee_minor is negative ({fee})")
        if net != gross - fee:
            raise ParseError(
                f"{where}: the report contradicts itself: net {net} != gross {gross} - fee {fee}"
            )
        if (line_type == "charge") != (gross > 0):
            raise ParseError(
                f"{where}: a {line_type} must have {'positive' if line_type == 'charge' else 'negative'}"
                f" gross, found {gross}"
            )
        records.append(SettlementRecord(
            row_number=row_number,
            balance_transaction_id=transaction_id,
            created_on=strict_date(f["created_on"], f"{where} created_on"),
            line_type=line_type,
            merchant_reference=_required(f, "merchant_reference", where),
            currency=_level1_currency(f, where),
            gross_minor=gross,
            fee_minor=fee,
            net_minor=net,
            payout_id=_required(f, "payout_id", where),
            payout_reference=_required(f, "payout_reference", where),
            payout_date=strict_date(f["payout_date"], f"{where} payout_date"),
        ))
    return records
