"""camt.053.001.02 bank statement parser.

Two steps, kept separate on purpose (D-051):

1. ``read_camt053`` validates against the official XSD (source S1) and extracts the elements
   listed in docs/design.md §7.3, for any currency and any statement shape the XSD allows.
   Amounts stay as the decimal text found in the file, because converting text to minor units
   needs the currency's ISO 4217 exponent, and the only exponent this project has sourced is
   EUR's (S6).
2. ``apply_level1_rules`` enforces what Level 1 supports, rejecting anything else (D-025):
   exactly one statement with a period; EUR only; booked, non-reversal entries; no batch
   bookings, no multi-transaction entries, no per-entry charges; amounts no more precise than
   cents; and opening balance + entries = closing balance.

``parse_bank`` is both steps. Every element name used here appears in the official XSD.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from lxml import etree

from recon import camt053
from recon.parse import LEVEL1_CURRENCY, LEVEL1_EXPONENT, ParseError, strict_date

NS = {"c": camt053.NAMESPACE}
_DECIMAL = re.compile(r"^\+?([0-9]*)(?:\.([0-9]*))?$")


# --- step 1: structural read ------------------------------------------------------------------

@dataclass(frozen=True)
class Balance:
    code: str                  # Tp/CdOrPrtry/Cd (or "PRTRY:<value>")
    amount_text: str           # Amt, as written
    currency: str              # Amt/@Ccy
    credit_debit: str          # CdtDbtInd
    on: date                   # Dt, date as stated


@dataclass(frozen=True)
class Entry:
    entry_index: int           # position of Ntry in document order, 1-based
    amount_text: str
    currency: str
    credit_debit: str
    status: str                # Sts
    reversal: bool             # RvslInd
    booking_date: date | None  # BookgDt (Dt, or date part of DtTm as stated)
    value_date: date | None
    ntry_ref: str | None
    acct_svcr_ref: str | None
    bank_tx_code: str          # "Domn/Fmly/SubFmly" or "PRTRY:<Cd>"
    has_batch: bool            # NtryDtls/Btch present
    has_charges: bool          # Chrgs on the entry or its transaction
    tx_details_count: int      # number of NtryDtls/TxDtls
    end_to_end_id: str | None
    remittance_ustrd: str | None
    debtor_name: str | None


@dataclass(frozen=True)
class Statement:
    stmt_id: str
    account_id: str            # Acct/Id/IBAN or Acct/Id/Othr/Id
    account_currency: str | None
    period_from: date | None
    period_to: date | None
    balances: tuple[Balance, ...]
    entries: tuple[Entry, ...]


@dataclass(frozen=True)
class Camt053Document:
    msg_id: str
    statements: tuple[Statement, ...]


def _text(element, path: str) -> str | None:
    found = element.find(path, NS)
    return None if found is None else (found.text or "")


def _date_choice(element, path: str) -> date | None:
    """DateAndDateTimeChoice: Dt, or the date part of DtTm as stated (no zone conversion, D-026)."""
    choice = element.find(path, NS)
    if choice is None:
        return None
    day = _text(choice, "c:Dt")
    if day is not None:
        return strict_date(day, path)
    return strict_date((_text(choice, "c:DtTm") or "")[:10], path)


def _bank_tx_code(ntry) -> str:
    domain = ntry.find("c:BkTxCd/c:Domn", NS)
    if domain is not None:
        return "/".join([
            _text(domain, "c:Cd") or "",
            _text(domain, "c:Fmly/c:Cd") or "",
            _text(domain, "c:Fmly/c:SubFmlyCd") or "",
        ])
    proprietary = _text(ntry, "c:BkTxCd/c:Prtry/c:Cd")
    if proprietary is not None:
        return f"PRTRY:{proprietary}"
    raise ParseError("Ntry/BkTxCd has neither Domn nor Prtry")


def read_camt053(raw: bytes) -> Camt053Document:
    try:
        root = camt053.validate(raw)
    except camt053.Camt053Error as exc:
        raise ParseError(f"bank: {exc}") from exc

    message = root.find("c:BkToCstmrStmt", NS)
    statements = []
    for stmt in message.iterfind("c:Stmt", NS):
        account = stmt.find("c:Acct", NS)
        balances = tuple(
            Balance(
                code=_text(bal, "c:Tp/c:CdOrPrtry/c:Cd")
                     or f"PRTRY:{_text(bal, 'c:Tp/c:CdOrPrtry/c:Prtry')}",
                amount_text=_text(bal, "c:Amt"),
                currency=bal.find("c:Amt", NS).get("Ccy"),
                credit_debit=_text(bal, "c:CdtDbtInd"),
                on=_date_choice(bal, "c:Dt"),
            )
            for bal in stmt.iterfind("c:Bal", NS)
        )
        entries = []
        for index, ntry in enumerate(stmt.iterfind("c:Ntry", NS), start=1):
            tx_details = ntry.findall("c:NtryDtls/c:TxDtls", NS)
            tx = tx_details[0] if len(tx_details) == 1 else None
            ustrd = [] if tx is None else [u.text or "" for u in tx.iterfind("c:RmtInf/c:Ustrd", NS)]
            entries.append(Entry(
                entry_index=index,
                amount_text=_text(ntry, "c:Amt"),
                currency=ntry.find("c:Amt", NS).get("Ccy"),
                credit_debit=_text(ntry, "c:CdtDbtInd"),
                status=_text(ntry, "c:Sts"),
                reversal=(_text(ntry, "c:RvslInd") or "false").strip() in ("true", "1"),
                booking_date=_date_choice(ntry, "c:BookgDt"),
                value_date=_date_choice(ntry, "c:ValDt"),
                ntry_ref=_text(ntry, "c:NtryRef"),
                acct_svcr_ref=_text(ntry, "c:AcctSvcrRef"),
                bank_tx_code=_bank_tx_code(ntry),
                has_batch=ntry.find("c:NtryDtls/c:Btch", NS) is not None,
                has_charges=(ntry.find("c:Chrgs", NS) is not None
                             or ntry.find("c:NtryDtls/c:TxDtls/c:Chrgs", NS) is not None),
                tx_details_count=len(tx_details),
                end_to_end_id=None if tx is None else _text(tx, "c:Refs/c:EndToEndId"),
                remittance_ustrd=" ".join(ustrd) if ustrd else None,
                debtor_name=None if tx is None else _text(tx, "c:RltdPties/c:Dbtr/c:Nm"),
            ))
        has_period = stmt.find("c:FrToDt", NS) is not None
        statements.append(Statement(
            stmt_id=_text(stmt, "c:Id"),
            account_id=_text(account, "c:Id/c:IBAN") or _text(account, "c:Id/c:Othr/c:Id"),
            account_currency=_text(account, "c:Ccy"),
            # ISODateTime; the date part is taken as stated, with no zone conversion (D-026).
            period_from=(strict_date(_text(stmt, "c:FrToDt/c:FrDtTm")[:10], "Stmt/FrToDt/FrDtTm")
                         if has_period else None),
            period_to=(strict_date(_text(stmt, "c:FrToDt/c:ToDtTm")[:10], "Stmt/FrToDt/ToDtTm")
                       if has_period else None),
            balances=balances,
            entries=tuple(entries),
        ))
    return Camt053Document(msg_id=_text(message, "c:GrpHdr/c:MsgId"), statements=tuple(statements))


# --- step 2: Level 1 rules --------------------------------------------------------------------

@dataclass(frozen=True)
class BankEntryRecord:
    entry_index: int
    amount_minor: int          # signed: CRDT > 0, DBIT < 0
    cdt_dbt_ind: str
    currency: str
    status: str
    booking_date: date
    value_date: date | None
    acct_svcr_ref: str | None
    ntry_ref: str | None
    bank_tx_code: str
    end_to_end_id: str | None
    remittance_ustrd: str | None
    debtor_name: str | None


@dataclass(frozen=True)
class BankStatementRecord:
    msg_id: str
    stmt_id: str
    account_id: str
    currency: str
    period_from: date
    period_to: date
    opening_minor: int
    closing_minor: int
    entries: tuple[BankEntryRecord, ...]


def to_minor(text: str, exponent: int, where: str) -> int:
    """xs:decimal text -> minor units, by string arithmetic. Rejects sub-minor-unit precision."""
    match = _DECIMAL.match(text.strip())
    if match is None or (match.group(1) == "" and not match.group(2)):
        raise ParseError(f"{where}: {text!r} is not a decimal amount")
    whole, fraction = match.group(1) or "0", match.group(2) or ""
    if fraction[exponent:].strip("0"):
        raise ParseError(
            f"{where}: {text!r} is more precise than the currency's {exponent} decimal places"
        )
    fraction = (fraction + "0" * exponent)[:exponent]
    return int(whole) * 10**exponent + (int(fraction) if fraction else 0)


def _signed(amount: int, credit_debit: str) -> int:
    return amount if credit_debit == "CRDT" else -amount


def apply_level1_rules(document: Camt053Document) -> BankStatementRecord:
    if len(document.statements) != 1:
        raise ParseError(
            f"bank: {len(document.statements)} Stmt elements; Level 1 accepts exactly one per file"
        )
    stmt = document.statements[0]
    if stmt.period_from is None or stmt.period_to is None:
        raise ParseError("bank: Stmt/FrToDt is missing; the statement period is required")
    if stmt.account_currency not in (None, LEVEL1_CURRENCY):
        raise ParseError(
            f"bank: account currency {stmt.account_currency} is not supported; Level 1 is "
            f"{LEVEL1_CURRENCY} only"
        )

    def eur_amount(text: str, currency: str, where: str) -> int:
        if currency != LEVEL1_CURRENCY:
            raise ParseError(
                f"bank {where}: currency {currency} is not supported; Level 1 is {LEVEL1_CURRENCY} only"
            )
        return to_minor(text, LEVEL1_EXPONENT, f"bank {where}")

    by_code: dict[str, list[Balance]] = {}
    for balance in stmt.balances:
        by_code.setdefault(balance.code, []).append(balance)
    opening_closing = {}
    for code in ("OPBD", "CLBD"):
        found = by_code.get(code, [])
        if len(found) != 1:
            raise ParseError(f"bank: expected exactly one {code} balance, found {len(found)}")
        b = found[0]
        opening_closing[code] = _signed(eur_amount(b.amount_text, b.currency, f"Bal {code}"),
                                        b.credit_debit)

    records = []
    for e in stmt.entries:
        where = f"Ntry #{e.entry_index}"
        if e.status != "BOOK":
            raise ParseError(f"bank {where}: status {e.status}; Level 1 accepts booked (BOOK) entries only")
        if e.reversal:
            raise ParseError(f"bank {where}: reversal entries (RvslInd=true) are not supported in Level 1")
        if e.has_batch:
            raise ParseError(f"bank {where}: batch-booked entry (NtryDtls/Btch) is not supported in Level 1")
        if e.tx_details_count > 1:
            raise ParseError(
                f"bank {where}: {e.tx_details_count} TxDtls in one entry; Level 1 accepts at most one"
            )
        if e.has_charges:
            raise ParseError(f"bank {where}: per-entry charges (Chrgs) are not supported in Level 1")
        if e.booking_date is None:
            raise ParseError(f"bank {where}: BookgDt is missing; the booking date is required")
        amount = _signed(eur_amount(e.amount_text, e.currency, where), e.credit_debit)
        if amount == 0:
            raise ParseError(f"bank {where}: amount is zero")
        records.append(BankEntryRecord(
            entry_index=e.entry_index,
            amount_minor=amount,
            cdt_dbt_ind=e.credit_debit,
            currency=e.currency,
            status=e.status,
            booking_date=e.booking_date,
            value_date=e.value_date,
            acct_svcr_ref=e.acct_svcr_ref,
            ntry_ref=e.ntry_ref,
            bank_tx_code=e.bank_tx_code,
            end_to_end_id=e.end_to_end_id,
            remittance_ustrd=e.remittance_ustrd,
            debtor_name=e.debtor_name,
        ))

    opening, closing = opening_closing["OPBD"], opening_closing["CLBD"]
    total = sum(r.amount_minor for r in records)
    if opening + total != closing:
        raise ParseError(
            f"bank: balances do not tie out: opening {opening} + entries {total} = "
            f"{opening + total}, but closing balance is {closing} (minor units)"
        )

    return BankStatementRecord(
        msg_id=document.msg_id,
        stmt_id=stmt.stmt_id,
        account_id=stmt.account_id,
        currency=LEVEL1_CURRENCY,
        period_from=stmt.period_from,
        period_to=stmt.period_to,
        opening_minor=opening,
        closing_minor=closing,
        entries=tuple(records),
    )


def parse_bank(raw: bytes) -> BankStatementRecord:
    return apply_level1_rules(read_camt053(raw))
