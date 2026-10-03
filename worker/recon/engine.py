"""The reconciliation engine: three raw files in, one canonical result out.

    reconcile(ledger_raw, settlement_raw, bank_raw) -> canonical result bytes

A pure function of its inputs and ENGINE_VERSION (D-007): it parses the raw bytes with the
same parsers the importer uses, never reads the database, the clock, the environment or any
random source, and iterates only over explicitly sorted sequences (design §5.3, §6).

Matching, in order (design §5.4–5.6):
  A. exact        direct bank transfer: same reference and amount, bank within 0..5 days
  B. gross_net    single-payment payout: ledger gross - bank credit == stated fee
  C. many_to_one  batched payout: every line has its ledger entry and the credit == sum of nets
Leftovers are classified (design §5.7 as amended by D-048) into exceptions, each with a
suggested reason and an explanation; every timing explanation states its deadline.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from recon.money import format_minor
from recon.parse import PARSER_VERSION, ParseError
from recon.parse.bank import BankEntryRecord, BankStatementRecord, parse_bank
from recon.parse.csvfiles import LedgerRecord, SettlementRecord, parse_ledger, parse_settlement

ENGINE_VERSION = "1.0.0"
RESULT_FORMAT = "recon-result/1"

EXACT_WINDOW_DAYS = 5
LEDGER_SETTLEMENT_WINDOW_DAYS = 1
PAYOUT_WINDOW_DAYS = 3

CONFIG = {
    "EXACT_WINDOW_DAYS": EXACT_WINDOW_DAYS,
    "LEDGER_SETTLEMENT_WINDOW_DAYS": LEDGER_SETTLEMENT_WINDOW_DAYS,
    "PAYOUT_WINDOW_DAYS": PAYOUT_WINDOW_DAYS,
}

# Month names spelled out here: strftime("%b") depends on the process locale (D-057).
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class EngineInputError(ValueError):
    """The inputs cannot be reconciled as given (rejected file or inconsistent data)."""


def _day(d: date) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def _eur(amount_minor: int) -> str:
    return f"EUR {format_minor(amount_minor)}"


def _days(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def _bank_ref(text: str | None) -> str | None:
    """Bank-side reference, trimmed of spaces exactly like SQL btrim() in the finish check."""
    return None if text is None else text.strip(" ")


# --- result -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class MatchResult:
    ordinal: int
    pass_name: str
    ledger: tuple[int, ...]        # ledger row numbers
    settlement: tuple[int, ...]    # settlement row numbers
    bank: tuple[int, ...]          # bank entry indexes
    explanation: str


@dataclass(frozen=True)
class ExceptionResult:
    ordinal: int
    reason: str
    ledger: tuple[int, ...]
    settlement: tuple[int, ...]
    bank: tuple[int, ...]
    explanation: str
    fingerprint: str               # sha256 hex of reason and sorted natural keys


@dataclass(frozen=True)
class EngineResult:
    inputs: dict[str, str]
    period_from: date
    period_to: date
    row_counts: dict[str, int]
    matches: tuple[MatchResult, ...]
    exceptions: tuple[ExceptionResult, ...]

    def document(self) -> dict:
        matches_by_pass: dict[str, int] = {}
        for m in self.matches:
            matches_by_pass[m.pass_name] = matches_by_pass.get(m.pass_name, 0) + 1
        by_reason: dict[str, int] = {}
        for e in self.exceptions:
            by_reason[e.reason] = by_reason.get(e.reason, 0) + 1
        return {
            "format": RESULT_FORMAT,
            "engine_version": ENGINE_VERSION,
            "parser_version": PARSER_VERSION,
            "config": dict(CONFIG),
            "inputs": dict(self.inputs),
            "statement": {"period_from": self.period_from.isoformat(),
                          "period_to": self.period_to.isoformat()},
            "matches": [
                {"ordinal": m.ordinal, "pass": m.pass_name, "ledger": list(m.ledger),
                 "settlement": list(m.settlement), "bank": list(m.bank),
                 "explanation": m.explanation}
                for m in self.matches
            ],
            "exceptions": [
                {"ordinal": e.ordinal, "reason": e.reason, "ledger": list(e.ledger),
                 "settlement": list(e.settlement), "bank": list(e.bank),
                 "explanation": e.explanation, "fingerprint": e.fingerprint}
                for e in self.exceptions
            ],
            "summary": {
                "rows": dict(self.row_counts),
                "matches_by_pass": matches_by_pass,
                "exceptions_by_reason": by_reason,
            },
        }

    def canonical(self) -> bytes:
        return canonical_json(self.document())


def canonical_json(document: object) -> bytes:
    """Sorted keys, no whitespace, ASCII only; floats are refused anywhere in the tree."""
    _refuse_floats(document)
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _refuse_floats(value: object) -> None:
    if isinstance(value, float):
        raise TypeError("a float reached the canonical result; amounts must be integer minor units")
    if isinstance(value, dict):
        for item in value.values():
            _refuse_floats(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _refuse_floats(item)


# --- entry points -----------------------------------------------------------------------------

def run_engine(ledger_raw: bytes, settlement_raw: bytes, bank_raw: bytes) -> EngineResult:
    try:
        ledger = parse_ledger(ledger_raw)
        settlement = parse_settlement(settlement_raw)
        statement = parse_bank(bank_raw)
    except ParseError as exc:
        raise EngineInputError(f"input rejected by the parser: {exc}") from exc
    inputs = {
        "ledger": hashlib.sha256(ledger_raw).hexdigest(),
        "settlement": hashlib.sha256(settlement_raw).hexdigest(),
        "bank": hashlib.sha256(bank_raw).hexdigest(),
    }
    return _Engine(ledger, settlement, statement, inputs).run()


def reconcile(ledger_raw: bytes, settlement_raw: bytes, bank_raw: bytes) -> bytes:
    return run_engine(ledger_raw, settlement_raw, bank_raw).canonical()


# --- the engine -------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Payout:
    reference: str
    payout_id: str
    payout_date: date
    lines: tuple[SettlementRecord, ...]


class _Engine:
    def __init__(self, ledger: list[LedgerRecord], settlement: list[SettlementRecord],
                 statement: BankStatementRecord, inputs: dict[str, str]) -> None:
        # Deterministic orders (design §5.3). Every loop below iterates one of these.
        self.ledger = sorted(ledger, key=lambda r: (r.booked_on, r.entry_id, r.row_number))
        self.settlement = sorted(settlement, key=lambda s: (
            s.payout_date, s.payout_reference, s.created_on, s.balance_transaction_id, s.row_number))
        self.bank = sorted(statement.entries, key=lambda e: (e.booking_date, e.entry_index))
        self.statement = statement
        self.inputs = inputs
        self.ledger_by_reference: dict[str, list[LedgerRecord]] = {}
        for row in self.ledger:
            self.ledger_by_reference.setdefault(row.payment_reference, []).append(row)
        self.payouts = self._payout_groups()

        self.used_ledger: set[int] = set()
        self.used_settlement: set[int] = set()
        self.used_bank: set[int] = set()
        self.ledger_match: dict[int, int] = {}          # ledger row -> match ordinal
        self.matches: list[MatchResult] = []
        self.exceptions: list[ExceptionResult] = []

    def _payout_groups(self) -> list[_Payout]:
        grouped: dict[str, list[SettlementRecord]] = {}
        for line in self.settlement:
            grouped.setdefault(line.payout_reference, []).append(line)
        payouts = []
        for reference, lines in grouped.items():
            dates = sorted({line.payout_date for line in lines})
            ids = sorted({line.payout_id for line in lines})
            if len(dates) != 1 or len(ids) != 1:
                raise EngineInputError(
                    f"payout reference {reference} is inconsistent in the settlement report: "
                    f"payout dates {[d.isoformat() for d in dates]}, payout ids {ids}"
                )
            payouts.append(_Payout(reference, ids[0], dates[0], tuple(lines)))
        payouts.sort(key=lambda p: (p.payout_date, p.reference))
        return payouts

    # --- lookups ------------------------------------------------------------------------------

    def _first_bank(self, accept: Callable[[BankEntryRecord], bool]) -> BankEntryRecord | None:
        for entry in self.bank:
            if entry.entry_index in self.used_bank:
                continue
            if accept(entry):
                return entry
        return None

    def _ledger_for_line(self, line: SettlementRecord, exclude: set[int]) -> LedgerRecord | None:
        """Pass B/C rule: same reference, card, amount == gross, dates within ±1 day."""
        for row in self.ledger_by_reference.get(line.merchant_reference, []):
            if row.row_number in self.used_ledger or row.row_number in exclude:
                continue
            if row.channel != "card":
                continue
            if row.amount_minor != line.gross_minor:
                continue
            if abs((row.booked_on - line.created_on).days) > LEDGER_SETTLEMENT_WINDOW_DAYS:
                continue
            return row
        return None

    def _payout_credit(self, payout: _Payout) -> BankEntryRecord | None:
        """Pass B/C rule: a credit carrying the payout reference, booked 0..3 days after."""
        latest = payout.payout_date + timedelta(days=PAYOUT_WINDOW_DAYS)
        return self._first_bank(lambda e: (
            e.amount_minor > 0
            and _bank_ref(e.end_to_end_id) == payout.reference
            and payout.payout_date <= e.booking_date <= latest
        ))

    # --- recording ----------------------------------------------------------------------------

    def _match(self, pass_name: str, ledger: list[LedgerRecord], settlement: list[SettlementRecord],
               bank: list[BankEntryRecord], explanation: str) -> None:
        ordinal = len(self.matches) + 1
        for row in ledger:
            self.used_ledger.add(row.row_number)
            self.ledger_match[row.row_number] = ordinal
        self.used_settlement.update(line.row_number for line in settlement)
        self.used_bank.update(entry.entry_index for entry in bank)
        self.matches.append(MatchResult(
            ordinal=ordinal,
            pass_name=pass_name,
            ledger=tuple(sorted(row.row_number for row in ledger)),
            settlement=tuple(sorted(line.row_number for line in settlement)),
            bank=tuple(sorted(entry.entry_index for entry in bank)),
            explanation=explanation,
        ))

    def _exception(self, reason: str, ledger: list[LedgerRecord], settlement: list[SettlementRecord],
                   bank: list[BankEntryRecord], explanation: str) -> None:
        self.used_ledger.update(row.row_number for row in ledger)
        self.used_settlement.update(line.row_number for line in settlement)
        self.used_bank.update(entry.entry_index for entry in bank)
        keys = (tuple(sorted(row.row_number for row in ledger)),
                tuple(sorted(line.row_number for line in settlement)),
                tuple(sorted(entry.entry_index for entry in bank)))
        fingerprint_source = reason + "|" + ";".join(
            f"{kind}:{','.join(str(k) for k in values)}"
            for kind, values in zip(("L", "S", "B"), keys)
        )
        self.exceptions.append(ExceptionResult(
            ordinal=len(self.exceptions) + 1,
            reason=reason,
            ledger=keys[0],
            settlement=keys[1],
            bank=keys[2],
            explanation=explanation,
            fingerprint=hashlib.sha256(fingerprint_source.encode("ascii")).hexdigest(),
        ))

    # --- passes -------------------------------------------------------------------------------

    def pass_exact(self) -> None:
        for row in self.ledger:
            if row.channel != "bank_transfer" or row.row_number in self.used_ledger:
                continue
            latest = row.booked_on + timedelta(days=EXACT_WINDOW_DAYS)
            entry = self._first_bank(lambda e: (
                _bank_ref(e.remittance_ustrd) == row.payment_reference
                and e.amount_minor == row.amount_minor
                and e.currency == row.currency
                and row.booked_on <= e.booking_date <= latest
            ))
            if entry is None:
                continue
            lag = (entry.booking_date - row.booked_on).days
            self._match("exact", [row], [], [entry], (
                f"Pass exact: ledger {row.entry_id} and bank entry #{entry.entry_index} "
                f"({entry.acct_svcr_ref}) share reference {row.payment_reference} and amount "
                f"{_eur(row.amount_minor)}; the bank booked it {_days(lag)} after the ledger date "
                f"(window {_days(EXACT_WINDOW_DAYS)})."
            ))

    def pass_gross_net(self) -> None:
        for payout in self.payouts:
            if len(payout.lines) != 1:
                continue
            (line,) = payout.lines
            row = self._ledger_for_line(line, set())
            entry = self._payout_credit(payout)
            if row is None or entry is None:
                continue
            if row.amount_minor - entry.amount_minor != line.fee_minor:
                continue
            self._match("gross_net", [row], [line], [entry], (
                f"Pass gross_net: ledger {row.entry_id} gross {_eur(row.amount_minor)} - bank "
                f"credit {_eur(entry.amount_minor)} (entry #{entry.entry_index}) = "
                f"{_eur(row.amount_minor - entry.amount_minor)}, equal to the fee stated on "
                f"settlement line {line.balance_transaction_id} for payout {payout.payout_id}."
            ))

    def pass_many_to_one(self) -> None:
        for payout in self.payouts:
            if len(payout.lines) < 2:
                continue
            chosen: list[LedgerRecord] = []
            for line in payout.lines:
                row = self._ledger_for_line(line, {c.row_number for c in chosen})
                if row is None:
                    break
                chosen.append(row)
            if len(chosen) != len(payout.lines):
                continue
            entry = self._payout_credit(payout)
            if entry is None:
                continue
            gross = sum(line.gross_minor for line in payout.lines)
            fees = sum(line.fee_minor for line in payout.lines)
            net = sum(line.net_minor for line in payout.lines)
            if entry.amount_minor != net:
                continue
            if sum(row.amount_minor for row in chosen) != gross:
                continue
            self._match("many_to_one", chosen, list(payout.lines), [entry], (
                f"Pass many_to_one: payout {payout.payout_id}: {len(payout.lines)} settlement lines "
                f"(gross {_eur(gross)}, fees {_eur(fees)}, net {_eur(net)}) match "
                f"{len(chosen)} ledger entries totalling {_eur(gross)} and one bank credit of "
                f"{_eur(entry.amount_minor)} (entry #{entry.entry_index})."
            ))

    # --- classification of leftovers (design §5.7, D-048) -------------------------------------

    def classify(self) -> None:
        period_to = self.statement.period_to
        self._classify_payouts(period_to)
        self._classify_transfers_with_bank_entry()
        self._classify_remaining_ledger(period_to)
        self._classify_remaining_bank()

    def _classify_payouts(self, period_to: date) -> None:
        for payout in self.payouts:
            lines = [l for l in payout.lines if l.row_number not in self.used_settlement]
            if not lines:
                continue
            ledger_rows: list[LedgerRecord] = []
            orphans: list[SettlementRecord] = []
            gross_differences: list[tuple[SettlementRecord, LedgerRecord]] = []
            claimed: set[int] = set()
            for line in lines:
                row = next((r for r in self.ledger_by_reference.get(line.merchant_reference, [])
                            if r.channel == "card" and r.row_number not in self.used_ledger
                            and r.row_number not in claimed), None)
                if row is None:
                    orphans.append(line)
                    continue
                claimed.add(row.row_number)
                ledger_rows.append(row)
                if row.amount_minor != line.gross_minor:
                    gross_differences.append((line, row))
            entry = self._first_bank(lambda e: (
                e.amount_minor > 0 and _bank_ref(e.end_to_end_id) == payout.reference))

            gross = sum(l.gross_minor for l in lines)
            fees = sum(l.fee_minor for l in lines)
            net = sum(l.net_minor for l in lines)
            deadline = payout.payout_date + timedelta(days=PAYOUT_WINDOW_DAYS)
            subject = (f"Payout {payout.payout_id} dated {_day(payout.payout_date)} "
                       f"({len(lines)} settlement line{'s' if len(lines) != 1 else ''}, net {_eur(net)})")

            if entry is None:
                if deadline > period_to:
                    reason = "timing"
                    explanation = (
                        f"{subject}: payout window {_days(PAYOUT_WINDOW_DAYS)}; expected at the bank "
                        f"by {_day(deadline)}; this statement ends {_day(period_to)}. If not booked "
                        f"by {_day(deadline)}, treat as missing."
                    )
                else:
                    reason = "missing_from_bank"
                    explanation = (
                        f"{subject}: no bank credit carries reference {payout.reference}. The payout "
                        f"window closed on {_day(deadline)}, inside this statement period "
                        f"(ends {_day(period_to)})."
                    )
            elif entry.amount_minor != net:
                reason = "amount_mismatch"
                received_gap = gross - entry.amount_minor
                explanation = (
                    f"{subject}: the report states gross {_eur(gross)}, fees {_eur(fees)}, net "
                    f"{_eur(net)}; bank entry #{entry.entry_index} received {_eur(entry.amount_minor)}. "
                    f"Gross - deposit = {_eur(received_gap)}; stated fees {_eur(fees)}; "
                    f"unexplained {_eur(received_gap - fees)}."
                )
            elif gross_differences:
                reason = "amount_mismatch"
                explanation = f"{subject}: ledger and processor disagree on gross amounts: " + "; ".join(
                    f"{row.entry_id} {_eur(row.amount_minor)} vs settlement "
                    f"{line.balance_transaction_id} {_eur(line.gross_minor)}"
                    for line, row in gross_differences) + "."
            elif orphans:
                reason = "missing_from_ledger"
                explanation = (
                    f"{subject}: the bank credit (entry #{entry.entry_index}) and the report agree on "
                    f"{_eur(net)}, but no ledger entry exists for " + ", ".join(
                        f"settlement line {l.balance_transaction_id} (reference "
                        f"{l.merchant_reference}, gross {_eur(l.gross_minor)})" for l in orphans) + "."
                )
            else:
                reason = "timing"
                facts = []
                if not (payout.payout_date <= entry.booking_date <= deadline):
                    facts.append(f"bank entry #{entry.entry_index} was booked {_day(entry.booking_date)}, "
                                 f"outside the payout window (expected by {_day(deadline)})")
                for line, row in zip(lines, ledger_rows):
                    if abs((row.booked_on - line.created_on).days) > LEDGER_SETTLEMENT_WINDOW_DAYS:
                        facts.append(f"ledger {row.entry_id} is dated {_day(row.booked_on)} but the "
                                     f"processor recorded {line.balance_transaction_id} on "
                                     f"{_day(line.created_on)} (window ±{_days(LEDGER_SETTLEMENT_WINDOW_DAYS)})")
                explanation = (f"{subject}: amounts agree, but " + "; ".join(facts) +
                               f". Payout window {_days(PAYOUT_WINDOW_DAYS)}; deadline "
                               f"{_day(deadline)}.")
            self._exception(reason, ledger_rows, lines, [entry] if entry else [], explanation)

    def _classify_transfers_with_bank_entry(self) -> None:
        for row in self.ledger:
            if row.row_number in self.used_ledger or row.channel != "bank_transfer":
                continue
            entry = self._first_bank(lambda e: _bank_ref(e.remittance_ustrd) == row.payment_reference)
            if entry is None:
                continue
            if entry.amount_minor != row.amount_minor:
                self._exception("amount_mismatch", [row], [], [entry], (
                    f"Ledger {row.entry_id} and bank entry #{entry.entry_index} share reference "
                    f"{row.payment_reference} but the amounts differ: ledger {_eur(row.amount_minor)}, "
                    f"bank {_eur(entry.amount_minor)} (difference "
                    f"{_eur(row.amount_minor - entry.amount_minor)})."
                ))
            else:
                deadline = row.booked_on + timedelta(days=EXACT_WINDOW_DAYS)
                self._exception("timing", [row], [], [entry], (
                    f"Ledger {row.entry_id} ({row.payment_reference}) booked {_day(row.booked_on)}; "
                    f"bank window {_days(EXACT_WINDOW_DAYS)}; expected at the bank by {_day(deadline)}; "
                    f"bank entry #{entry.entry_index} with the same reference and amount was booked "
                    f"{_day(entry.booking_date)}, outside that window."
                ))

    def _classify_remaining_ledger(self, period_to: date) -> None:
        for row in self.ledger:
            if row.row_number in self.used_ledger:
                continue
            original = next((o for o in self.ledger_by_reference[row.payment_reference]
                             if o.row_number != row.row_number
                             and o.amount_minor == row.amount_minor
                             and o.row_number in self.ledger_match), None)
            if original is not None:
                self._exception("possible_duplicate", [row], [], [], (
                    f"Ledger {row.entry_id} repeats reference {row.payment_reference} and amount "
                    f"{_eur(row.amount_minor)} of ledger {original.entry_id}, which is reconciled in "
                    f"match #{self.ledger_match[original.row_number]}. Probable duplicate entry."
                ))
                continue
            if row.channel == "bank_transfer":
                window, where = EXACT_WINDOW_DAYS, "at the bank"
            else:
                window, where = LEDGER_SETTLEMENT_WINDOW_DAYS, "in the processor's settlement report"
            deadline = row.booked_on + timedelta(days=window)
            subject = (f"Ledger {row.entry_id} ({row.payment_reference}, {row.channel.replace('_', ' ')}, "
                       f"{_eur(row.amount_minor)}) booked {_day(row.booked_on)}")
            if deadline > period_to:
                self._exception("timing", [row], [], [], (
                    f"{subject}: window {_days(window)}; expected {where} by {_day(deadline)}; this "
                    f"statement ends {_day(period_to)}. If not there by {_day(deadline)}, treat as "
                    f"missing."
                ))
            else:
                self._exception("missing_from_bank", [row], [], [], (
                    f"{subject}: not found {where}. The window closed on {_day(deadline)}, inside "
                    f"this statement period (ends {_day(period_to)})."
                ))

    def _classify_remaining_bank(self) -> None:
        for entry in self.bank:
            if entry.entry_index in self.used_bank:
                continue
            detail = (f"bank entry #{entry.entry_index} ({entry.acct_svcr_ref}) {_eur(abs(entry.amount_minor))} "
                      f"booked {_day(entry.booking_date)}, end-to-end id {entry.end_to_end_id or 'none'}, "
                      f"remittance '{entry.remittance_ustrd or ''}'")
            if entry.amount_minor > 0:
                self._exception("unknown_deposit", [], [], [entry], (
                    f"Unknown deposit: {detail}. It matches no ledger entry and no payout."))
            else:
                self._exception("unexplained_debit", [], [], [entry], (
                    f"Unexplained debit: {detail}. It matches no ledger entry and no payout."))

    # --- run ----------------------------------------------------------------------------------

    def run(self) -> EngineResult:
        self.pass_exact()
        self.pass_gross_net()
        self.pass_many_to_one()
        self.classify()
        self._assert_every_row_allocated_once()
        return EngineResult(
            inputs=self.inputs,
            period_from=self.statement.period_from,
            period_to=self.statement.period_to,
            row_counts={"ledger": len(self.ledger), "settlement": len(self.settlement),
                        "bank": len(self.bank)},
            matches=tuple(self.matches),
            exceptions=tuple(self.exceptions),
        )

    def _assert_every_row_allocated_once(self) -> None:
        for kind, rows, field in (
            ("ledger", [r.row_number for r in self.ledger], "ledger"),
            ("settlement", [s.row_number for s in self.settlement], "settlement"),
            ("bank", [e.entry_index for e in self.bank], "bank"),
        ):
            cited = [k for item in (*self.matches, *self.exceptions) for k in getattr(item, field)]
            if sorted(cited) != sorted(rows):
                raise RuntimeError(f"engine bug: {kind} rows are not each allocated exactly once")
