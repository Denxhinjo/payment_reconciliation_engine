"""Synthetic month generator: ledger CSV, Orrery Payments settlement CSV, camt.053 XML.

Everything produced here is SYNTHETIC DEMO DATA. Orrery Payments and Demo Bank are fictional;
customers are numbered codes; no IBANs are generated (D-004).

The generator is not part of the engine, so it may use randomness, but only a seeded
``random.Random``: the same seed and month always produce byte-identical files. It never
reads the clock; every timestamp in the output is derived from the month.

Background activity (about 600 card payments, daily payouts, refunds, direct bank
transfers) reconciles cleanly. On top of it the planted problems from docs/design.md §8
are placed by fixed rules, and ``planted.json`` records each one with the rows involved
and the outcome the engine is expected to produce.
"""

from __future__ import annotations

import calendar
import csv
import hashlib
import io
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from lxml import etree

from recon import camt053
from recon.money import format_minor

GENERATOR_VERSION = "1.0.0"
CURRENCY = "EUR"
LABEL = "SYNTHETIC DEMO DATA"

LEDGER_FILENAME = "synthetic_ledger_{month}.csv"
SETTLEMENT_FILENAME = "synthetic_orrery_settlement_{month}.csv"
BANK_FILENAME = "synthetic_bank_camt053_{month}.xml"
PLANTED_FILENAME = "planted.json"

LEDGER_HEADER = [
    "entry_id", "booked_on", "entry_type", "channel", "payment_reference", "currency",
    "amount_minor", "customer_ref", "description",
]
SETTLEMENT_HEADER = [
    "report_version", "balance_transaction_id", "created_on", "line_type", "merchant_reference",
    "currency", "gross_minor", "fee_minor", "net_minor", "payout_id", "payout_reference",
    "payout_date",
]

LEDGER_BANNER = f"# {LABEL} - fictional company, fictional customers. Not real transactions."
SETTLEMENT_BANNER = (
    f"# {LABEL} - Orrery Payments is a fictional processor. Not a real provider's output."
)
BANK_BANNER = (
    f" {LABEL} - Demo Bank and Orrery Payments are fictional. Not real transactions or accounts. "
)

ACCOUNT_ID = "SYNTHETIC-DEMO-ACCT-0001"
OPENING_BALANCE_MINOR = 5_000_000          # EUR 50,000.00, synthetic
PLANTED_FEE_SHORTFALL_MINOR = 40           # P2: the bank receives EUR 0.40 less than net

PROCESSOR_NAME = "ORRERY PAYMENTS (FICTIONAL)"
OWNER_NAME = "SYNTHETIC DEMO FINTECH (FICTIONAL)"
BANK_NAME = "DEMO BANK (FICTIONAL)"
# Received SEPA credit transfer: Domain PMNT / Family RCDT / Sub-family ESCT (source S4).
BANK_TX_CODE = ("PMNT", "RCDT", "ESCT")


def orrery_fee(gross_minor: int) -> int:
    """Fictional Orrery price: 1.4% + EUR 0.25, 1.4% rounded half-up to the cent (integers)."""
    return 25 + (gross_minor * 14 + 500) // 1000


def roll_to_weekday(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def payout_date_for(created_on: date) -> date:
    """Orrery pays out each day's activity on T+2, rolled forward past weekends."""
    return roll_to_weekday(created_on + timedelta(days=2))


# --- in-memory rows ---------------------------------------------------------------------------

@dataclass
class LedgerRow:
    order: tuple
    booked_on: date
    entry_type: str
    channel: str
    payment_reference: str
    amount_minor: int
    customer_ref: str
    description: str
    entry_id: str = ""


@dataclass
class SettlementRow:
    order: tuple
    created_on: date
    line_type: str
    merchant_reference: str
    gross_minor: int
    fee_minor: int
    payout_id: str
    payout_reference: str
    payout_date: date
    balance_transaction_id: str = ""

    @property
    def net_minor(self) -> int:
        return self.gross_minor - self.fee_minor


@dataclass
class BankEntry:
    order: tuple
    booking_date: date
    amount_minor: int
    end_to_end_id: str | None
    ustrd: str
    debtor_name: str
    acct_svcr_ref: str = ""


@dataclass
class Payout:
    payout_id: str
    payout_reference: str
    created_on: date
    payout_date: date
    lines: list[SettlementRow] = field(default_factory=list)

    @property
    def net_minor(self) -> int:
        return sum(line.net_minor for line in self.lines)


@dataclass
class GeneratedMonth:
    month: str
    seed: int
    ledger_csv: bytes
    settlement_csv: bytes
    bank_xml: bytes
    planted_json: bytes

    def files(self) -> dict[str, bytes]:
        return {
            LEDGER_FILENAME.format(month=self.month): self.ledger_csv,
            SETTLEMENT_FILENAME.format(month=self.month): self.settlement_csv,
            BANK_FILENAME.format(month=self.month): self.bank_xml,
            PLANTED_FILENAME: self.planted_json,
        }

    def write(self, directory: Path) -> list[Path]:
        directory.mkdir(parents=True, exist_ok=True)
        written = []
        for name, content in self.files().items():
            path = directory / name
            path.write_bytes(content)
            written.append(path)
        return written


# --- generation -------------------------------------------------------------------------------

def _first_weekday_on_or_after(day: date, weekday: int) -> date:
    return day + timedelta(days=(weekday - day.weekday()) % 7)


def generate(seed: int, month: str) -> GeneratedMonth:
    year, month_number = (int(part) for part in month.split("-"))
    first = date(year, month_number, 1)
    last = date(year, month_number, calendar.monthrange(year, month_number)[1])
    days = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
    rng = random.Random(seed)

    # Planted days, chosen by fixed rules so they are explainable for any month.
    single_payment_days = [d for d in days if d.weekday() == 6 and payout_date_for(d) <= last]
    fee_mismatch_day = single_payment_days[1]                                   # P2
    missing_payout_day = _first_weekday_on_or_after(first + timedelta(days=7), 0)   # P1 (Monday)
    batch_50_day = _first_weekday_on_or_after(first + timedelta(days=14), 2)       # P3 (Wednesday)
    duplicate_day = _first_weekday_on_or_after(first + timedelta(days=19), 3)      # P4 (Thursday)
    unknown_deposit_day = _first_weekday_on_or_after(first + timedelta(days=20), 1)  # P5 (Tuesday)
    special_days = {fee_mismatch_day, missing_payout_day, batch_50_day, duplicate_day,
                    *single_payment_days}

    ledger: list[LedgerRow] = []
    settlement: list[SettlementRow] = []
    bank: list[BankEntry] = []
    payouts: dict[date, Payout] = {}
    card_payments: list[tuple[LedgerRow, SettlementRow]] = []
    reference_counter = 0
    order = 0

    def next_order() -> int:
        nonlocal order
        order += 1
        return order

    def next_reference() -> str:
        nonlocal reference_counter
        reference_counter += 1
        return f"PAY-{reference_counter:06d}"

    def payout_for(created_on: date) -> Payout:
        if created_on not in payouts:
            payout_id = f"PO-{created_on:%Y%m%d}"
            payouts[created_on] = Payout(
                payout_id=payout_id,
                payout_reference=f"ORR-{payout_id}",
                created_on=created_on,
                payout_date=payout_date_for(created_on),
            )
        return payouts[created_on]

    # Direct bank transfers: ~40 over the month; one booked on the last day arrives next month.
    transfer_days = sorted(rng.choice(days[:-1]) for _ in range(39)) + [last]
    transfers: list[tuple[LedgerRow, BankEntry | None]] = []

    # Card payments: a few ledger rows are booked one day before the processor's created_on
    # (time-zone edge, inside the ±1 day window), on ordinary mid-month days.
    ordinary_days = [d for d in days[4:-5] if d not in special_days]
    early_booked_days = set(rng.sample(ordinary_days, 3))

    for day in days:
        for _ in range(transfer_days.count(day)):
            reference = next_reference()
            amount = rng.randint(2_000, 250_000)
            customer = f"SYN-CUST-{rng.randint(1, 400):04d}"
            row = LedgerRow((day, next_order()), day, "payment", "bank_transfer", reference,
                            amount, customer, "Synthetic invoice paid by bank transfer")
            ledger.append(row)
            if day == last:
                transfers.append((row, None))   # natural timing: the bank books it next month
                continue
            booking = roll_to_weekday(day + timedelta(days=rng.randint(0, 2)))
            if booking > last:
                booking = day if day.weekday() < 5 else roll_to_weekday(day)
            entry = BankEntry((booking, next_order()), booking, amount, None, reference,
                              f"Synthetic Customer {customer[-4:]}")
            bank.append(entry)
            transfers.append((row, entry))

        if day in single_payment_days:
            count = 1
        elif day == missing_payout_day:
            count = 9
        elif day == batch_50_day:
            count = 50
        else:
            count = rng.randint(12, 30)

        payout = payout_for(day)
        early_index = rng.randrange(count) if day in early_booked_days else None
        for index in range(count):
            reference = next_reference()
            gross = rng.randint(500, 40_000)
            customer = f"SYN-CUST-{rng.randint(1, 400):04d}"
            booked_on = day - timedelta(days=1) if index == early_index else day
            ledger_row = LedgerRow((booked_on, next_order()), booked_on, "payment", "card",
                                   reference, gross, customer, "Synthetic order")
            line = SettlementRow((day, next_order()), day, "charge", reference, gross,
                                 orrery_fee(gross), payout.payout_id, payout.payout_reference,
                                 payout.payout_date)
            ledger.append(ledger_row)
            settlement.append(line)
            payout.lines.append(line)
            card_payments.append((ledger_row, line))

    # Refunds: six, on ordinary busy days, each of an earlier, distinct card payment. Netted
    # inside that day's payout, as processors do.
    refund_days = sorted(rng.sample([d for d in ordinary_days if d not in early_booked_days], 6))
    refunded: set[str] = set()
    for day in refund_days:
        candidates = [
            (row, line) for row, line in card_payments
            if line.created_on < day and row.payment_reference not in refunded
        ]
        original_row, original_line = rng.choice(candidates)
        refunded.add(original_row.payment_reference)
        amount = original_line.gross_minor if rng.random() < 0.5 else original_line.gross_minor // 2
        reference = f"{original_row.payment_reference}-R1"
        payout = payout_for(day)
        ledger.append(LedgerRow((day, next_order()), day, "refund", "card", reference, -amount,
                                original_row.customer_ref,
                                f"Synthetic refund of {original_row.payment_reference}"))
        line = SettlementRow((day, next_order()), day, "refund", reference, -amount, 0,
                             payout.payout_id, payout.payout_reference, payout.payout_date)
        settlement.append(line)
        payout.lines.append(line)

    # P4: one card payment on the duplicate day is written to the ledger twice (a retried
    # write): different entry id, same reference, amount, date and customer.
    duplicate_candidates = [
        row for row, line in card_payments
        if line.created_on == duplicate_day and row.payment_reference not in refunded
    ]
    duplicated = rng.choice(duplicate_candidates)
    duplicate = LedgerRow((duplicated.booked_on, duplicated.order[1], 1), duplicated.booked_on,
                          "payment", "card", duplicated.payment_reference,
                          duplicated.amount_minor, duplicated.customer_ref, duplicated.description)
    ledger.append(duplicate)

    # Payouts reach the bank as one credit each, except the planted and timing ones.
    payout_bank_entries: dict[str, BankEntry] = {}
    for created_on, payout in sorted(payouts.items()):
        if payout.payout_date > last:
            continue                                 # natural timing: arrives next month
        if created_on == missing_payout_day:
            continue                                 # P1: the payout never arrives
        booking = payout.payout_date
        if rng.random() < 0.25:
            delayed = roll_to_weekday(booking + timedelta(days=1))
            if delayed <= last:
                booking = delayed
        amount = payout.net_minor
        if created_on == fee_mismatch_day:
            amount -= PLANTED_FEE_SHORTFALL_MINOR    # P2
        entry = BankEntry((booking, next_order()), booking, amount, payout.payout_reference,
                          f"ORRERY PAYOUT {payout.payout_id}", PROCESSOR_NAME)
        bank.append(entry)
        payout_bank_entries[payout.payout_reference] = entry

    # P5: a credit nobody expected.
    unknown = BankEntry((unknown_deposit_day, next_order()), unknown_deposit_day, 25_000, None,
                        "SYNTHETIC UNREFERENCED TRANSFER", "Synthetic Sender 9001")
    bank.append(unknown)

    # Stable ordering and identifiers.
    ledger.sort(key=lambda r: (r.booked_on, r.order[1:]))
    for number, row in enumerate(ledger, start=1):
        row.entry_id = f"LE-{number:06d}"
    settlement.sort(key=lambda r: (r.created_on, r.order))
    for number, line in enumerate(settlement, start=1):
        line.balance_transaction_id = f"BT-{number:07d}"
    bank.sort(key=lambda e: (e.booking_date, e.order))
    for number, entry in enumerate(bank, start=1):
        entry.acct_svcr_ref = f"SYNBANK-{first:%Y%m}-{number:06d}"

    ledger_csv = _ledger_csv(ledger)
    settlement_csv = _settlement_csv(settlement)
    bank_xml = _bank_xml(bank, first, last)

    by_reference = {}
    for row in ledger:
        by_reference.setdefault(row.payment_reference, []).append(row)

    def ledger_ids(lines: list[SettlementRow]) -> list[str]:
        return [by_reference[line.merchant_reference][0].entry_id for line in lines]

    def bt_ids(lines: list[SettlementRow]) -> list[str]:
        return [line.balance_transaction_id for line in lines]

    p1 = payouts[missing_payout_day]
    p2 = payouts[fee_mismatch_day]
    p3 = payouts[batch_50_day]
    p2_entry = payout_bank_entries[p2.payout_reference]
    p3_entry = payout_bank_entries[p3.payout_reference]
    timing_payouts = [p for _, p in sorted(payouts.items()) if p.payout_date > last]
    timing_transfer = next(row for row, entry in transfers if entry is None)

    planted = {
        "label": f"{LABEL}. Orrery Payments and Demo Bank are fictional.",
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "month": month,
        "statement_period": {"from": first.isoformat(), "to": last.isoformat()},
        "fee_rule": "Orrery (fictional): 1.4% rounded half-up to the cent, plus EUR 0.25",
        "files": {
            LEDGER_FILENAME.format(month=month): hashlib.sha256(ledger_csv).hexdigest(),
            SETTLEMENT_FILENAME.format(month=month): hashlib.sha256(settlement_csv).hexdigest(),
            BANK_FILENAME.format(month=month): hashlib.sha256(bank_xml).hexdigest(),
        },
        "counts": {
            "ledger_rows": len(ledger),
            "card_payments": len(card_payments),
            "card_refunds": len(refund_days),
            "direct_bank_transfers": len(transfers),
            "settlement_rows": len(settlement),
            "payouts": len(payouts),
            "bank_entries": len(bank),
        },
        "planted": [
            {
                "id": "P1",
                "problem": "Missing payout",
                "how": f"Payout {p1.payout_id} ({len(p1.lines)} payments, net "
                       f"{CURRENCY} {format_minor(p1.net_minor)}, payout date "
                       f"{p1.payout_date.isoformat()}) is in the settlement report but never "
                       "reaches the bank.",
                "expected": {"kind": "exception", "reason": "missing_from_bank",
                             "ledger": ledger_ids(p1.lines), "settlement": bt_ids(p1.lines),
                             "bank": []},
            },
            {
                "id": "P2",
                "problem": "Fee mismatch",
                "how": f"Single-payment payout {p2.payout_id}: report states gross "
                       f"{CURRENCY} {format_minor(p2.lines[0].gross_minor)}, fee "
                       f"{format_minor(p2.lines[0].fee_minor)}, net "
                       f"{format_minor(p2.net_minor)}; the bank received "
                       f"{format_minor(p2_entry.amount_minor)} "
                       f"({format_minor(PLANTED_FEE_SHORTFALL_MINOR)} less).",
                "expected": {"kind": "exception", "reason": "amount_mismatch",
                             "ledger": ledger_ids(p2.lines), "settlement": bt_ids(p2.lines),
                             "bank": [p2_entry.acct_svcr_ref]},
            },
            {
                "id": "P3",
                "problem": "50-to-1 batch deposit",
                "how": f"{len(p3.lines)} card payments on {batch_50_day.isoformat()} are paid "
                       f"out as one bank credit of {CURRENCY} {format_minor(p3.net_minor)} "
                       f"(payout {p3.payout_id}).",
                "expected": {"kind": "match", "pass": "many_to_one",
                             "ledger": ledger_ids(p3.lines), "settlement": bt_ids(p3.lines),
                             "bank": [p3_entry.acct_svcr_ref]},
            },
            {
                "id": "P4",
                "problem": "Duplicate ledger entry",
                "how": f"Payment {duplicated.payment_reference} ({CURRENCY} "
                       f"{format_minor(duplicated.amount_minor)}) is recorded twice in the "
                       f"ledger: {duplicated.entry_id} and {duplicate.entry_id}. The payment "
                       "itself was collected and paid out once.",
                "expected": {"kind": "exception", "reason": "possible_duplicate",
                             "ledger": [duplicate.entry_id], "settlement": [], "bank": [],
                             "original_matched_ledger_entry": duplicated.entry_id},
            },
            {
                "id": "P5",
                "problem": "Deposit nobody expected",
                "how": f"A {CURRENCY} {format_minor(unknown.amount_minor)} credit on "
                       f"{unknown_deposit_day.isoformat()} with remittance text "
                       f"'{unknown.ustrd}' has no counterpart in the ledger or the report.",
                "expected": {"kind": "exception", "reason": "unknown_deposit",
                             "ledger": [], "settlement": [], "bank": [unknown.acct_svcr_ref]},
            },
        ],
        "natural_timing": [
            {
                "id": f"T{number}",
                "what": f"Payout {p.payout_id} for card payments on {p.created_on.isoformat()} "
                        f"is dated {p.payout_date.isoformat()}, after the statement period.",
                "expected": {"kind": "exception", "reason": "timing",
                             "ledger": ledger_ids(p.lines), "settlement": bt_ids(p.lines),
                             "bank": []},
            }
            for number, p in enumerate(timing_payouts, start=1)
        ] + [
            {
                "id": f"T{len(timing_payouts) + 1}",
                "what": f"Bank transfer {timing_transfer.payment_reference} booked in the ledger "
                        f"on {timing_transfer.booked_on.isoformat()} arrives at the bank after "
                        "the statement period.",
                "expected": {"kind": "exception", "reason": "timing",
                             "ledger": [timing_transfer.entry_id], "settlement": [], "bank": []},
            }
        ],
    }
    planted_json = (json.dumps(planted, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()

    return GeneratedMonth(month=month, seed=seed, ledger_csv=ledger_csv,
                          settlement_csv=settlement_csv, bank_xml=bank_xml,
                          planted_json=planted_json)


# --- serialisation ----------------------------------------------------------------------------

def _csv_bytes(banner: str, header: list[str], rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    buffer.write(banner + "\n")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _ledger_csv(rows: list[LedgerRow]) -> bytes:
    return _csv_bytes(LEDGER_BANNER, LEDGER_HEADER, [
        [r.entry_id, r.booked_on.isoformat(), r.entry_type, r.channel, r.payment_reference,
         CURRENCY, str(r.amount_minor), r.customer_ref, r.description]
        for r in rows
    ])


def _settlement_csv(rows: list[SettlementRow]) -> bytes:
    return _csv_bytes(SETTLEMENT_BANNER, SETTLEMENT_HEADER, [
        ["1", r.balance_transaction_id, r.created_on.isoformat(), r.line_type,
         r.merchant_reference, CURRENCY, str(r.gross_minor), str(r.fee_minor), str(r.net_minor),
         r.payout_id, r.payout_reference, r.payout_date.isoformat()]
        for r in rows
    ])


def _bank_xml(entries: list[BankEntry], first: date, last: date) -> bytes:
    ns = camt053.NAMESPACE

    def add(parent, tag: str, text: str | None = None, **attributes) -> etree._Element:
        element = etree.SubElement(parent, f"{{{ns}}}{tag}", attributes)
        if text is not None:
            element.text = text
        return element

    created = f"{(last + timedelta(days=1)).isoformat()}T05:00:00Z"
    message_id = f"SYN-CAMT053-{first:%Y%m}"

    document = etree.Element(f"{{{ns}}}Document", nsmap={None: ns})
    statement_msg = add(document, "BkToCstmrStmt")

    header = add(statement_msg, "GrpHdr")
    add(header, "MsgId", message_id)
    add(header, "CreDtTm", created)
    pagination = add(header, "MsgPgntn")
    add(pagination, "PgNb", "1")
    add(pagination, "LastPgInd", "true")
    add(header, "AddtlInf", BANK_BANNER.strip())

    statement = add(statement_msg, "Stmt")
    add(statement, "Id", f"SYN-STMT-{first:%Y%m}")
    add(statement, "CreDtTm", created)
    period = add(statement, "FrToDt")
    add(period, "FrDtTm", f"{first.isoformat()}T00:00:00Z")
    add(period, "ToDtTm", f"{last.isoformat()}T23:59:59Z")

    account = add(statement, "Acct")
    add(add(add(account, "Id"), "Othr"), "Id", ACCOUNT_ID)
    add(account, "Ccy", CURRENCY)
    add(add(account, "Ownr"), "Nm", OWNER_NAME)
    add(add(add(account, "Svcr"), "FinInstnId"), "Nm", BANK_NAME)

    closing = OPENING_BALANCE_MINOR + sum(e.amount_minor for e in entries)
    for code, amount, on in (("OPBD", OPENING_BALANCE_MINOR, first - timedelta(days=1)),
                             ("CLBD", closing, last)):
        balance = add(statement, "Bal")
        add(add(add(balance, "Tp"), "CdOrPrtry"), "Cd", code)
        add(balance, "Amt", format_minor(abs(amount)), Ccy=CURRENCY)
        add(balance, "CdtDbtInd", "CRDT" if amount >= 0 else "DBIT")
        add(add(balance, "Dt"), "Dt", on.isoformat())

    for entry in entries:
        ntry = add(statement, "Ntry")
        add(ntry, "Amt", format_minor(abs(entry.amount_minor)), Ccy=CURRENCY)
        add(ntry, "CdtDbtInd", "CRDT" if entry.amount_minor > 0 else "DBIT")
        add(ntry, "Sts", "BOOK")
        add(add(ntry, "BookgDt"), "Dt", entry.booking_date.isoformat())
        add(add(ntry, "ValDt"), "Dt", entry.booking_date.isoformat())
        add(ntry, "AcctSvcrRef", entry.acct_svcr_ref)
        domain = add(add(ntry, "BkTxCd"), "Domn")
        add(domain, "Cd", BANK_TX_CODE[0])
        family = add(domain, "Fmly")
        add(family, "Cd", BANK_TX_CODE[1])
        add(family, "SubFmlyCd", BANK_TX_CODE[2])
        details = add(add(ntry, "NtryDtls"), "TxDtls")
        if entry.end_to_end_id is not None:
            add(add(details, "Refs"), "EndToEndId", entry.end_to_end_id)
        add(add(add(details, "RltdPties"), "Dbtr"), "Nm", entry.debtor_name)
        add(add(details, "RmtInf"), "Ustrd", entry.ustrd)

    tree = etree.ElementTree(document)
    document.addprevious(etree.Comment(BANK_BANNER))
    xml = etree.tostring(tree, xml_declaration=True, encoding="UTF-8", pretty_print=True)
    camt053.validate(xml)   # never write a bank file that fails the official schema
    return xml
