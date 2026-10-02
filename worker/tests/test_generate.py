"""Synthetic generator: determinism, official-schema validity, labelling, and planted problems.

The central test (``test_files_disagree_exactly_where_planted``) does not trust planted.json:
it re-derives from the three files every place where they disagree, then requires that set
to equal the planted and timing items. An accidental discrepancy introduced by the
generator would make the demo "catch" a problem nobody planted, so it must fail here.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import pytest
from lxml import etree

from recon import camt053
from recon.generate import (
    BANK_FILENAME, LEDGER_FILENAME, PLANTED_FILENAME, SETTLEMENT_FILENAME, generate, orrery_fee,
)
from recon.money import format_minor

SEED = 20260901
MONTH = "2026-09"
DEMO_DIR = Path(__file__).resolve().parents[2] / "demo-data" / MONTH
NS = {"c": camt053.NAMESPACE}
PAYOUT_WINDOW_DAYS = 3
EXACT_WINDOW_DAYS = 5


@pytest.fixture(scope="module")
def month():
    return generate(SEED, MONTH)


def _csv_rows(content: bytes) -> list[dict[str, str]]:
    lines = content.decode("utf-8").splitlines()
    assert lines[0].startswith("#")
    return list(csv.DictReader(lines[1:]))


@pytest.fixture(scope="module")
def data(month):
    ledger = _csv_rows(month.ledger_csv)
    settlement = _csv_rows(month.settlement_csv)
    root = etree.fromstring(month.bank_xml, camt053.safe_parser())
    bank = []
    for ntry in root.iterfind(".//c:Ntry", NS):
        sign = 1 if ntry.findtext("c:CdtDbtInd", namespaces=NS) == "CRDT" else -1
        bank.append({
            "ref": ntry.findtext("c:AcctSvcrRef", namespaces=NS),
            "amount": sign * _minor(ntry.findtext("c:Amt", namespaces=NS)),
            "date": date.fromisoformat(ntry.findtext("c:BookgDt/c:Dt", namespaces=NS)),
            "e2e": ntry.findtext("c:NtryDtls/c:TxDtls/c:Refs/c:EndToEndId", namespaces=NS),
            "ustrd": ntry.findtext("c:NtryDtls/c:TxDtls/c:RmtInf/c:Ustrd", namespaces=NS),
        })
    planted = json.loads(month.planted_json)
    return ledger, settlement, bank, root, planted


def _minor(text: str) -> int:
    """Test-local decimal-to-cents conversion (string arithmetic only)."""
    whole, _, fraction = text.partition(".")
    assert len(fraction) <= 2
    return int(whole) * 100 + int((fraction + "00")[:2])


# --- money helper ---------------------------------------------------------------------------

@pytest.mark.parametrize("minor, text", [(0, "0.00"), (5, "0.05"), (123450, "1234.50"),
                                         (-5, "-0.05"), (-100, "-1.00")])
def test_format_minor(minor, text):
    assert format_minor(minor) == text


@pytest.mark.parametrize("bad", [1.5, True, "100"])
def test_format_minor_refuses_non_integers(bad):
    with pytest.raises(TypeError):
        format_minor(bad)


@pytest.mark.parametrize("gross, fee", [(10000, 165), (31685, 469), (500, 32), (35, 25)])
def test_orrery_fee_rule(gross, fee):
    """1.4% rounded half-up to the cent, plus EUR 0.25 (e.g. 316.85 -> 4.44 + 0.25)."""
    assert orrery_fee(gross) == fee


# --- determinism ------------------------------------------------------------------------------

def test_same_seed_and_month_give_byte_identical_files(month):
    assert generate(SEED, MONTH).files() == month.files()


def test_different_seed_gives_different_files(month):
    other = generate(SEED + 1, MONTH).files()
    assert all(other[name] != content for name, content in month.files().items())


def test_committed_demo_data_is_exactly_what_the_generator_produces(month):
    """The committed demo files are reproducible from seed and month, byte for byte (this
    also catches any line-ending rewrite by git)."""
    for name, content in month.files().items():
        assert (DEMO_DIR / name).read_bytes() == content, name


def test_planted_json_records_the_hashes_of_the_three_files(month, data):
    *_, planted = data
    for name, content in month.files().items():
        if name != PLANTED_FILENAME:
            assert planted["files"][name] == hashlib.sha256(content).hexdigest()


# --- format validity --------------------------------------------------------------------------

def test_bank_file_validates_against_the_official_xsd(month):
    camt053.validate(month.bank_xml)


def test_validator_rejects_a_schema_violation(month):
    """Removing the mandatory Sts element must fail validation: the validator is real."""
    broken = re.sub(rb"<Sts>BOOK</Sts>", b"", month.bank_xml, count=1)
    with pytest.raises(camt053.Camt053Error, match="XSD validation"):
        camt053.validate(broken)


def test_validator_refuses_a_schema_file_with_the_wrong_hash(monkeypatch):
    camt053.schema.cache_clear()
    monkeypatch.setattr(camt053, "SCHEMA_SHA256", "0" * 64)
    try:
        with pytest.raises(camt053.Camt053Error, match="schema"):
            camt053.schema()
    finally:
        camt053.schema.cache_clear()


def test_validator_does_not_expand_external_entities(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("SHOULD-NOT-APPEAR")
    document = (
        f'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "{secret.as_uri()}">]>'
        f'<Document xmlns="{camt053.NAMESPACE}"><BkToCstmrStmt>&x;</BkToCstmrStmt></Document>'
    ).encode()
    with pytest.raises(camt053.Camt053Error, match="DTD") as info:
        camt053.validate(document)
    assert "SHOULD-NOT-APPEAR" not in str(info.value)


def test_csv_headers_are_exact(month):
    assert month.ledger_csv.decode().splitlines()[1] == (
        "entry_id,booked_on,entry_type,channel,payment_reference,currency,amount_minor,"
        "customer_ref,description"
    )
    assert month.settlement_csv.decode().splitlines()[1] == (
        "report_version,balance_transaction_id,created_on,line_type,merchant_reference,currency,"
        "gross_minor,fee_minor,net_minor,payout_id,payout_reference,payout_date"
    )


def test_amounts_are_integer_minor_units_in_eur(data):
    ledger, settlement, bank, root, _ = data
    integer = re.compile(r"^-?[0-9]+$")
    for row in ledger:
        assert row["currency"] == "EUR" and integer.match(row["amount_minor"])
    for row in settlement:
        assert row["currency"] == "EUR"
        assert all(integer.match(row[k]) for k in ("gross_minor", "fee_minor", "net_minor"))
    assert {a.get("Ccy") for a in root.iterfind(".//c:Amt", NS)} == {"EUR"}


# --- labelling and synthetic-only -------------------------------------------------------------

def test_every_file_is_labelled_synthetic(month, data):
    *_, root, planted = data
    assert month.ledger_csv.startswith(b"# SYNTHETIC DEMO DATA")
    assert month.settlement_csv.startswith(b"# SYNTHETIC DEMO DATA")
    assert b"<!-- SYNTHETIC DEMO DATA" in month.bank_xml.split(b"<Document", 1)[0]
    assert root.findtext("c:BkToCstmrStmt/c:GrpHdr/c:AddtlInf", namespaces=NS).startswith(
        "SYNTHETIC DEMO DATA")
    assert planted["label"].startswith("SYNTHETIC DEMO DATA")


def test_no_iban_shaped_strings_anywhere(month):
    iban_shape = re.compile(rb"\b[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}\b")
    for name, content in month.files().items():
        assert iban_shape.findall(content) == [], name


def test_account_is_a_synthetic_other_id_not_an_iban(data):
    *_, root, _ = data
    account = root.find("c:BkToCstmrStmt/c:Stmt/c:Acct/c:Id", NS)
    assert account.find("c:IBAN", NS) is None
    assert account.findtext("c:Othr/c:Id", namespaces=NS) == "SYNTHETIC-DEMO-ACCT-0001"


# --- internal consistency ---------------------------------------------------------------------

def test_bank_statement_balances_tie_out(data):
    _, _, bank, root, _ = data
    balances = {}
    for bal in root.iterfind(".//c:Stmt/c:Bal", NS):
        sign = 1 if bal.findtext("c:CdtDbtInd", namespaces=NS) == "CRDT" else -1
        balances[bal.findtext("c:Tp/c:CdOrPrtry/c:Cd", namespaces=NS)] = sign * _minor(
            bal.findtext("c:Amt", namespaces=NS))
    assert balances["OPBD"] + sum(e["amount"] for e in bank) == balances["CLBD"]


def test_settlement_lines_are_consistent_and_follow_the_fee_rule(data):
    _, settlement, *_ = data
    for row in settlement:
        gross, fee, net = int(row["gross_minor"]), int(row["fee_minor"]), int(row["net_minor"])
        assert net == gross - fee
        if row["line_type"] == "charge":
            assert gross > 0 and fee == orrery_fee(gross)
        else:
            assert row["line_type"] == "refund" and gross < 0 and fee == 0


def test_all_dates_fall_in_the_month_and_statement_period(data):
    ledger, settlement, bank, *_ = data
    first, last = date(2026, 9, 1), date(2026, 9, 30)
    assert all(first <= date.fromisoformat(r["booked_on"]) <= last for r in ledger)
    assert all(first <= date.fromisoformat(r["created_on"]) <= last for r in settlement)
    assert all(first <= e["date"] <= last for e in bank)


def test_identifiers_are_unique(data):
    ledger, settlement, bank, *_ = data
    assert len({r["entry_id"] for r in ledger}) == len(ledger)
    assert len({r["balance_transaction_id"] for r in settlement}) == len(settlement)
    assert len({e["ref"] for e in bank}) == len(bank)


# --- planted problems -------------------------------------------------------------------------

def _discrepancies(ledger, settlement, bank, period_to: date) -> dict[str, set]:
    """Every disagreement between the files, derived independently of planted.json.

    Returns label -> set of row identifiers, where labels describe the *kind* of
    disagreement (not the planted ids).
    """
    found: dict[str, set] = defaultdict(set)
    ledger_by_ref = defaultdict(list)
    for row in ledger:
        ledger_by_ref[row["payment_reference"]].append(row)
    bank_by_e2e = defaultdict(list)
    bank_by_ustrd = defaultdict(list)
    for entry in bank:
        if entry["e2e"]:
            bank_by_e2e[entry["e2e"]].append(entry)
        else:
            bank_by_ustrd[entry["ustrd"]].append(entry)

    # Duplicate ledger references (same reference, amount and date).
    for ref, rows in ledger_by_ref.items():
        if len(rows) > 1:
            keys = {(r["amount_minor"], r["booked_on"]) for r in rows}
            assert len(keys) == 1, f"{ref} repeats with different amount or date"
            found["duplicate_ledger_reference"] |= {r["entry_id"] for r in rows[1:]}

    # Every settlement line has its ledger row (same reference, amount = gross, ±1 day).
    for line in settlement:
        rows = ledger_by_ref.get(line["merchant_reference"], [])
        created = date.fromisoformat(line["created_on"])
        assert rows and all(
            int(r["amount_minor"]) == int(line["gross_minor"])
            and abs((date.fromisoformat(r["booked_on"]) - created).days) <= 1
            for r in rows
        ), f"settlement {line['balance_transaction_id']} has no consistent ledger row"

    # Payouts against bank credits.
    payouts = defaultdict(list)
    for line in settlement:
        payouts[line["payout_reference"]].append(line)
    for payout_ref, lines in payouts.items():
        payout_date = date.fromisoformat(lines[0]["payout_date"])
        net = sum(int(l["net_minor"]) for l in lines)
        entries = bank_by_e2e.pop(payout_ref, [])
        ids = frozenset(l["balance_transaction_id"] for l in lines)
        if not entries:
            label = "payout_after_period" if payout_date > period_to else "payout_never_arrived"
            found[label].add(ids)
            continue
        assert len(entries) == 1
        entry = entries[0]
        assert payout_date <= entry["date"] <= payout_date + timedelta(days=PAYOUT_WINDOW_DAYS)
        if entry["amount"] != net:
            found["payout_amount_differs"].add((ids, entry["ref"], net - entry["amount"]))

    # Direct transfers against bank credits.
    for row in ledger:
        if row["channel"] != "bank_transfer":
            continue
        entries = bank_by_ustrd.pop(row["payment_reference"], [])
        if not entries:
            found["transfer_not_in_bank"].add(row["entry_id"])
            continue
        assert len(entries) == 1
        entry = entries[0]
        booked = date.fromisoformat(row["booked_on"])
        assert entry["amount"] == int(row["amount_minor"])
        assert booked <= entry["date"] <= booked + timedelta(days=EXACT_WINDOW_DAYS)

    # Whatever bank credits are left were expected by nobody.
    leftovers = [e for es in list(bank_by_e2e.values()) + list(bank_by_ustrd.values()) for e in es]
    found["bank_entry_unexplained"] |= {e["ref"] for e in leftovers}
    return found


def test_files_disagree_exactly_where_planted(data):
    ledger, settlement, bank, root, planted = data
    period_to = date.fromisoformat(planted["statement_period"]["to"])
    found = _discrepancies(ledger, settlement, bank, period_to)
    items = {p["id"]: p["expected"] for p in planted["planted"] + planted["natural_timing"]}

    assert found["duplicate_ledger_reference"] == set(items["P4"]["ledger"])
    assert found["payout_never_arrived"] == {frozenset(items["P1"]["settlement"])}
    ((p2_lines, p2_bank, p2_shortfall),) = found["payout_amount_differs"]
    assert (set(p2_lines), p2_bank, p2_shortfall) == (
        set(items["P2"]["settlement"]), items["P2"]["bank"][0], 40)
    assert found["bank_entry_unexplained"] == set(items["P5"]["bank"])
    timing_payouts = {frozenset(e["settlement"]) for k, e in items.items()
                      if k.startswith("T") and e["settlement"]}
    assert found["payout_after_period"] == timing_payouts
    timing_transfers = {e["ledger"][0] for k, e in items.items()
                        if k.startswith("T") and not e["settlement"]}
    assert found["transfer_not_in_bank"] == timing_transfers
    assert set(found) == {"duplicate_ledger_reference", "payout_never_arrived",
                          "payout_amount_differs", "bank_entry_unexplained",
                          "payout_after_period", "transfer_not_in_bank"}


def test_p2_is_a_single_payment_payout(data):
    *_, planted = data
    p2 = next(p for p in planted["planted"] if p["id"] == "P2")["expected"]
    assert len(p2["settlement"]) == len(p2["ledger"]) == len(p2["bank"]) == 1


def test_p3_is_exactly_fifty_payments_into_one_deposit(data):
    ledger, settlement, bank, _, planted = data
    p3 = next(p for p in planted["planted"] if p["id"] == "P3")["expected"]
    assert len(p3["settlement"]) == len(p3["ledger"]) == 50 and len(p3["bank"]) == 1
    lines = [s for s in settlement if s["balance_transaction_id"] in set(p3["settlement"])]
    deposit = next(e for e in bank if e["ref"] == p3["bank"][0])
    assert deposit["amount"] == sum(int(s["net_minor"]) for s in lines)
    assert all(s["line_type"] == "charge" for s in lines)


def test_p4_original_and_duplicate_are_adjacent_distinct_entries(data):
    ledger, *_, planted = data
    p4 = next(p for p in planted["planted"] if p["id"] == "P4")["expected"]
    by_id = {r["entry_id"]: r for r in ledger}
    original, duplicate = by_id[p4["original_matched_ledger_entry"]], by_id[p4["ledger"][0]]
    assert original["entry_id"] < duplicate["entry_id"]
    assert {k: v for k, v in original.items() if k != "entry_id"} == \
           {k: v for k, v in duplicate.items() if k != "entry_id"}


def test_clean_single_payment_payouts_exist_for_the_gross_net_pass(data):
    _, settlement, _, _, planted = data
    payouts = defaultdict(list)
    for line in settlement:
        payouts[line["payout_reference"]].append(line)
    p2 = set(next(p for p in planted["planted"] if p["id"] == "P2")["expected"]["settlement"])
    clean_singles = [ls for ls in payouts.values()
                     if len(ls) == 1 and ls[0]["balance_transaction_id"] not in p2]
    assert len(clean_singles) >= 3


def test_background_includes_refunds_netted_inside_payouts(data):
    _, settlement, *_ = data
    refunds = [s for s in settlement if s["line_type"] == "refund"]
    assert len(refunds) == 6
    payout_sizes = defaultdict(int)
    for line in settlement:
        payout_sizes[line["payout_reference"]] += 1
    assert all(payout_sizes[r["payout_reference"]] > 1 for r in refunds)


def test_every_planted_row_exists(data):
    ledger, settlement, bank, _, planted = data
    ledger_ids = {r["entry_id"] for r in ledger}
    settlement_ids = {s["balance_transaction_id"] for s in settlement}
    bank_ids = {e["ref"] for e in bank}
    for item in planted["planted"] + planted["natural_timing"]:
        expected = item["expected"]
        assert set(expected["ledger"]) <= ledger_ids, item["id"]
        assert set(expected["settlement"]) <= settlement_ids, item["id"]
        assert set(expected["bank"]) <= bank_ids, item["id"]


def test_cli_generate_writes_the_four_files(tmp_path, month):
    from recon.__main__ import main

    assert main(["generate", "--seed", str(SEED), "--month", MONTH, "--out", str(tmp_path)]) == 0
    for name, content in month.files().items():
        assert (tmp_path / name).read_bytes() == content
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([
        LEDGER_FILENAME.format(month=MONTH), SETTLEMENT_FILENAME.format(month=MONTH),
        BANK_FILENAME.format(month=MONTH), PLANTED_FILENAME])


def test_readme_lists_every_planted_problem_with_its_rows(data):
    """The README's planted-problem table must stay in step with planted.json."""
    *_, planted = data
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    for item in planted["planted"] + planted["natural_timing"]:
        assert f"| {item['id']} |" in readme, item["id"]
        assert f"`{item['expected']['reason' if 'reason' in item['expected'] else 'pass']}`" in readme
    # Every payout and ledger id named in planted.json's descriptions appears in the README.
    for item in planted["planted"] + planted["natural_timing"]:
        text = item.get("how") or item["what"]
        for identifier in re.findall(r"\b(?:PO-\d{8}|LE-\d{6}|PAY-\d{6})\b", text):
            assert identifier in readme, f"{item['id']}: {identifier} missing from README"
