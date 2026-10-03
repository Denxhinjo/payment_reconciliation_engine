"""Hand-built reconciliation scenarios for engine tests. All values are synthetic.

A Scenario collects ledger rows, settlement lines and bank entries, then writes the three files
in their real formats. The bank file goes through the generator's camt.053 writer, so every
scenario's statement validates against the official XSD and ties out. Results are translated
back from the engine's natural keys to business ids (entry_id, balance_transaction_id,
AcctSvcrRef), so tests read like the scenario they describe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from recon.engine import EngineResult, run_engine
from recon.generate import (
    BankEntry, LedgerRow, SettlementRow, _bank_xml, _ledger_csv, _settlement_csv, orrery_fee,
)

PERIOD_FROM = date(2026, 9, 1)
PERIOD_TO = date(2026, 9, 30)


def d(day: int, month: int = 9) -> date:
    return date(2026, month, day)


@dataclass
class Outcome:
    matches: list[dict]
    exceptions: list[dict]
    result: EngineResult

    def only_exception(self) -> dict:
        assert len(self.exceptions) == 1, self.exceptions
        return self.exceptions[0]

    def match_passes(self) -> list[str]:
        return [m["pass"] for m in self.matches]


@dataclass
class Scenario:
    ledger: list[LedgerRow] = field(default_factory=list)
    settlement: list[SettlementRow] = field(default_factory=list)
    bank: list[BankEntry] = field(default_factory=list)

    # --- rows ------------------------------------------------------------------------------

    def ledger_row(self, reference: str, amount: int, booked_on: date, *, channel: str = "card",
                   entry_type: str | None = None) -> str:
        row = LedgerRow((booked_on, len(self.ledger)), booked_on,
                        entry_type or ("payment" if amount > 0 else "refund"), channel, reference,
                        amount, "SYN-CUST-0001", "Synthetic test row")
        row.entry_id = f"LE-{len(self.ledger) + 1:06d}"
        self.ledger.append(row)
        return row.entry_id

    def settlement_line(self, reference: str, gross: int, payout: str, payout_date: date, *,
                        created_on: date, fee: int | None = None) -> str:
        fee = (orrery_fee(gross) if gross > 0 else 0) if fee is None else fee
        line = SettlementRow((created_on, len(self.settlement)), created_on,
                             "charge" if gross > 0 else "refund", reference, gross, fee,
                             payout.replace("ORR-", ""), payout, payout_date)
        line.balance_transaction_id = f"BT-{len(self.settlement) + 1:07d}"
        self.settlement.append(line)
        return line.balance_transaction_id

    def bank_entry(self, amount: int, booking_date: date, *, end_to_end_id: str | None = None,
                   ustrd: str = "SYNTHETIC") -> str:
        entry = BankEntry((booking_date, len(self.bank)), booking_date, amount, end_to_end_id,
                          ustrd, "Synthetic Sender")
        entry.acct_svcr_ref = f"SYNBANK-T-{len(self.bank) + 1:06d}"
        self.bank.append(entry)
        return entry.acct_svcr_ref

    # --- composites ------------------------------------------------------------------------

    def transfer(self, reference: str, amount: int, booked_on: date,
                 bank_on: date | None = None, bank_amount: int | None = None) -> tuple[str, str | None]:
        """A direct bank transfer; bank_on=None means the bank never booked it."""
        entry_id = self.ledger_row(reference, amount, booked_on, channel="bank_transfer")
        bank_ref = None
        if bank_on is not None:
            bank_ref = self.bank_entry(amount if bank_amount is None else bank_amount, bank_on,
                                       ustrd=reference)
        return entry_id, bank_ref

    def payout(self, payout: str, payout_date: date, payments: list[tuple[str, int]], *,
               created_on: date, bank_on: date | None = None, bank_delta: int = 0,
               ledger: bool = True) -> dict:
        """Card payments created on one day, paid out together. bank_on=None: never arrives.
        bank_delta is added to the credited amount (e.g. -40 for a short payment)."""
        ids = {"ledger": [], "settlement": [], "bank": []}
        net = 0
        for reference, gross in payments:
            if ledger:
                ids["ledger"].append(self.ledger_row(reference, gross, created_on))
            bt = self.settlement_line(reference, gross, payout, payout_date, created_on=created_on)
            ids["settlement"].append(bt)
            line = self.settlement[-1]
            net += line.net_minor
        if bank_on is not None:
            ids["bank"].append(self.bank_entry(net + bank_delta, bank_on, end_to_end_id=payout,
                                               ustrd=f"ORRERY PAYOUT {payout}"))
        return ids

    # --- run -------------------------------------------------------------------------------

    FILLER_PAYOUT = "ORR-PO-FILLER"

    def files(self) -> tuple[bytes, bytes, bytes]:
        # The CSV parsers reject files without data rows. A scenario about transfers alone gets
        # one clean single-payment payout, which run() leaves out of the reported outcome.
        if not self.settlement:
            self.payout(self.FILLER_PAYOUT, d(3), [("PAY-FILLER", 10000)], created_on=d(1), bank_on=d(3))
        return (_ledger_csv(self.ledger), _settlement_csv(self.settlement),
                _bank_xml(self.bank, PERIOD_FROM, PERIOD_TO))

    def run(self) -> Outcome:
        result = run_engine(*self.files())
        ledger_ids = {n: row.entry_id for n, row in enumerate(self.ledger, start=1)}
        settlement_ids = {n: line.balance_transaction_id for n, line in enumerate(self.settlement, start=1)}
        bank_ids = {n: entry.acct_svcr_ref for n, entry in enumerate(self.bank, start=1)}

        def named(item) -> dict:
            return {
                "ledger": sorted(ledger_ids[k] for k in item.ledger),
                "settlement": sorted(settlement_ids[k] for k in item.settlement),
                "bank": sorted(bank_ids[k] for k in item.bank),
                "explanation": item.explanation,
            }

        filler = {i for i, line in settlement_ids.items()
                  if self.settlement[i - 1].payout_reference == self.FILLER_PAYOUT}
        return Outcome(
            matches=[{"pass": m.pass_name, **named(m)} for m in result.matches
                     if not filler.intersection(m.settlement)],
            exceptions=[{"reason": e.reason, **named(e)} for e in result.exceptions],
            result=result,
        )
