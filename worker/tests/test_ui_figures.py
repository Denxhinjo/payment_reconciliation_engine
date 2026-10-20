"""The figures the redesigned UI shows are decided by the database (D-096, D-097), and the demo
month's planted problems are reference data checked against planted.json."""

from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest

from recon import seed
from recon.migrate import DEFAULT_MIGRATIONS_DIR, migrate
from tests.conftest import APPEND_ONLY, fresh_database, raises_sqlstate

PLANTED = json.loads((Path(__file__).resolve().parents[2] / "demo-data" / "2026-09" / "planted.json").read_text(encoding="utf-8"))
MIGRATION_0014 = DEFAULT_MIGRATIONS_DIR / "0014_ui_figures.sql"


@pytest.fixture(scope="module")
def seeded_url():
    with fresh_database() as url:
        migrate(url)
        with psycopg.connect(url, autocommit=True) as conn:
            seed.seed_demo(conn, expected_sha256=seed.golden_sha256())
        yield url


@pytest.fixture
def db(seeded_url):
    with psycopg.connect(seeded_url) as conn:
        conn.execute("SELECT 1")
        try:
            yield conn
        finally:
            conn.rollback()


def test_run_figures_for_the_demo_month(db):
    row = db.execute(
        "SELECT period_label, ledger_rows, settlement_rows, settlement_payouts, bank_entries, rows_compared, "
        "open_needing_review, open_timing, resolved, open_value_under_review_minor, latest_replay_run_id, "
        "latest_replay_outcome FROM run_figures WHERE run_id = 1").fetchone()
    assert row == ("September 2026", 653, 612, 30, 67, 1332, 4, 3, 0, 237384, 2, "identical")
    assert db.execute("SELECT run_id FROM latest_run").fetchone() == (1,)


def test_amount_at_issue_per_exception(db):
    """D-097: missing payout = settlement net; mismatch = |expected - received|; duplicate = the
    ledger amount; unknown deposit = the credit; timing has none."""
    rows = dict(db.execute(
        "SELECT suggested_reason || ':' || reference, amount_at_issue_minor FROM exception_figures WHERE run_id = 1"))
    assert rows == {
        "amount_mismatch:PO-20260913": 40,
        "missing_from_bank:PO-20260914": 175223,
        "possible_duplicate:LE-000527 · PAY-000520": 37121,
        "unknown_deposit:SYNBANK-202609-000049": 25000,
        "timing:PO-20260929": None,
        "timing:PO-20260930": None,
        "timing:PAY-000625": None,
    }
    assert db.execute(
        "SELECT expected_minor, received_minor, shortfall_minor, settlement_gross_minor, settlement_fee_minor "
        "FROM exception_figures WHERE run_id = 1 AND suggested_reason = 'amount_mismatch'").fetchone() \
        == (31216, 31176, 40, 31685, 469)


def test_value_under_review_follows_resolutions(db):
    (target, staff) = db.execute(
        "SELECT (SELECT exception_id FROM exception_figures WHERE run_id = 1 AND suggested_reason = 'unknown_deposit'), "
        "(SELECT id FROM staff_user WHERE display_name = 'Demo Analyst 1')").fetchone()
    db.execute("INSERT INTO resolution (exception_id, reason_code, note, resolved_by) "
               "VALUES (%s, 'funds_identified_and_posted', 'Synthetic: sender identified.', %s)", (target, staff))
    assert db.execute("SELECT open_needing_review, resolved, open_value_under_review_minor FROM run_figures "
                      "WHERE run_id = 1").fetchone() == (3, 1, 237384 - 25000)
    assert db.execute("SELECT other_open_in_run FROM exception_figures WHERE exception_id = %s",
                      (target,)).fetchone() == (6,)


def test_every_planted_problem_and_timing_item_is_caught_in_the_demo_run(db):
    rows = db.execute("SELECT item_id, caught, outcome_ordinal FROM run_planted_items WHERE run_id = 1 "
                      "ORDER BY item_id").fetchall()
    assert [r[0] for r in rows] == ["P1", "P2", "P3", "P4", "P5", "T1", "T2", "T3"]
    assert all(caught for _, caught, _ in rows)


def test_a_planted_item_that_did_not_happen_is_not_caught(db):
    """The check is real: an item whose rows differ from the run's outcome is reported as missed."""
    db.execute("ALTER TABLE demo_planted_item DISABLE TRIGGER demo_planted_item_append_only")
    db.execute("UPDATE demo_planted_item SET ledger_keys = ledger_keys[2:] WHERE item_id = 'P1'")
    assert db.execute("SELECT caught FROM run_planted_items WHERE run_id = 1 AND item_id = 'P1'").fetchone() == (False,)


def test_planted_reference_data_equals_planted_json(db):
    expected = {}
    for category, items in (("planted", PLANTED["planted"]), ("timing", PLANTED["natural_timing"])):
        for it in items:
            e = it["expected"]
            expected[it["id"]] = (category, it.get("problem") or "Expected timing difference",
                                  it.get("how") or it["what"], e["kind"], e.get("reason") or e.get("pass"),
                                  sorted(e["ledger"]), sorted(e["settlement"]), sorted(e["bank"]))
    actual = {r[0]: tuple(r[1:]) for r in db.execute(
        "SELECT item_id, category, title, description, expected_kind, expected_outcome, ledger_keys, "
        "settlement_keys, bank_keys FROM demo_planted_item")}
    assert actual == expected
    files = PLANTED["files"]
    assert db.execute("SELECT encode(ledger_sha256, 'hex'), encode(settlement_sha256, 'hex'), "
                      "encode(bank_sha256, 'hex') FROM demo_planted_files").fetchall() == [
        (files["synthetic_ledger_2026-09.csv"], files["synthetic_orrery_settlement_2026-09.csv"],
         files["synthetic_bank_camt053_2026-09.xml"])]


def test_planted_reference_data_is_append_only(db):
    with raises_sqlstate(db, APPEND_ONLY):
        db.execute("UPDATE demo_planted_item SET title = 'x'")
    with raises_sqlstate(db, APPEND_ONLY):
        db.execute("DELETE FROM demo_planted_files")


def test_planted_items_apply_only_to_runs_of_the_demo_files():
    """A run of other files shows no planted problems."""
    with fresh_database() as url:
        migrate(url)
        with psycopg.connect(url) as conn:
            assert conn.execute("SELECT count(*) FROM run_planted_items").fetchone() == (0,)


def test_job_results_and_counts(db):
    assert db.execute("SELECT * FROM job_counts").fetchone() == (5, 0, 0, 0, 5)
    assert db.execute("SELECT job_id, file_rows, run_matches, run_exceptions, run_replay_outcome FROM job_results "
                      "ORDER BY job_id").fetchall() == [
        (1, 653, None, None, None), (2, 612, None, None, None), (3, 67, None, None, None),
        (4, None, 65, 7, None), (5, None, 65, 7, "identical")]
    assert db.execute("SELECT * FROM import_file_counts").fetchone() == (3, 3, 0, 0)


def test_migration_0014_is_safe_to_run_twice(db):
    db.execute(MIGRATION_0014.read_text(encoding="utf-8"))
    assert db.execute("SELECT count(*) FROM demo_planted_item").fetchone() == (8,)
    assert db.execute("SELECT rows_compared FROM run_figures WHERE run_id = 1").fetchone() == (1332,)
