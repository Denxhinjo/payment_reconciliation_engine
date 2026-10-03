"""Exceptions queue (stage 5): listing, evidence, resolving, correcting, every refusal in plain
words, and the two concurrency races decided by the database."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import psycopg
import pytest

from recon import importer, resolutions, runs
from recon.migrate import migrate
from tests.conftest import fresh_database

DEMO = Path(__file__).resolve().parents[2] / "demo-data" / "2026-09"
FILES = {
    "ledger": DEMO / "synthetic_ledger_2026-09.csv",
    "settlement": DEMO / "synthetic_orrery_settlement_2026-09.csv",
    "bank": DEMO / "synthetic_bank_camt053_2026-09.xml",
}
NOTE = "Synthetic: processor confirmed a EUR 0.40 adjustment, credit note requested."


def _load_demo_run(conn) -> tuple[int, list[int]]:
    staff = importer.seed_synthetic_staff(conn)
    ids = {}
    for kind, path in FILES.items():
        stored = importer.store_file(conn, kind, path.name, path.read_bytes(), staff[0])
        importer.process_parse_job(conn, stored.job_id)
        ids[kind] = stored.file_id
    outcome = runs.create_and_run(conn, ids["ledger"], ids["settlement"], ids["bank"])
    assert outcome.status == "finished"
    return outcome.run_id, staff


@pytest.fixture
def demo(conn):
    run_id, staff = _load_demo_run(conn)
    by_reason = {}
    for item in resolutions.queue(conn, run_id):
        by_reason.setdefault(item.suggested_reason, []).append(item.exception_id)
    return {"run": run_id, "staff": staff, "p1": by_reason["missing_from_bank"][0],
            "p2": by_reason["amount_mismatch"][0], "p4": by_reason["possible_duplicate"][0],
            "p5": by_reason["unknown_deposit"][0], "timing": by_reason["timing"]}


# --- the queue --------------------------------------------------------------------------------

def test_queue_lists_the_runs_seven_exceptions_open_in_order(conn, demo):
    items = resolutions.queue(conn, demo["run"])
    assert [i.ordinal for i in items] == list(range(1, 8))
    assert all(i.status == "open" and i.resolution_count == 0 for i in items)
    assert sorted(i.suggested_reason for i in items) == [
        "amount_mismatch", "missing_from_bank", "possible_duplicate", "timing", "timing", "timing",
        "unknown_deposit"]


def test_queue_status_filter(conn, demo):
    resolutions.resolve(conn, demo["p2"], "processor_error_claim_raised", NOTE, demo["staff"][0])
    assert len(resolutions.queue(conn, demo["run"], "open")) == 6
    assert [i.exception_id for i in resolutions.queue(conn, demo["run"], "resolved")] == [demo["p2"]]
    with pytest.raises(ValueError):
        resolutions.queue(conn, demo["run"], "closed")


def test_evidence_shows_the_rows_behind_an_exception(conn, demo):
    p2 = resolutions.evidence(conn, demo["p2"])
    assert [(r.source, r.amount_minor) for r in p2] == [
        ("ledger", 31685), ("settlement", 31685), ("bank", 31176)]
    assert "fee 469, net 31216, payout PO-20260913" in p2[1].detail
    p1 = resolutions.evidence(conn, demo["p1"])
    assert [r.source for r in p1].count("ledger") == 9 and [r.source for r in p1].count("settlement") == 9
    p5 = resolutions.evidence(conn, demo["p5"])
    assert [(r.source, r.amount_minor, r.reference) for r in p5] == [
        ("bank", 25000, "SYNTHETIC UNREFERENCED TRANSFER")]


# --- resolving and correcting -----------------------------------------------------------------

def test_resolving_marks_the_exception_resolved_with_who_why_and_note(conn, demo):
    resolution_id = resolutions.resolve(conn, demo["p2"], "processor_error_claim_raised", NOTE,
                                        demo["staff"][0])
    item = resolutions.queue_item(conn, demo["p2"])
    assert (item.status, item.current_resolution_id, item.current_reason_code, item.current_note,
            item.resolved_by, item.resolution_count) == (
        "resolved", resolution_id, "processor_error_claim_raised", NOTE, "Demo Analyst 1", 1)
    (record,) = resolutions.history(conn, demo["p2"])
    assert record.in_force and record.reason_label == "Processor error: claim raised with processor"


def test_correction_supersedes_and_keeps_the_original_unchanged(conn, demo):
    first = resolutions.resolve(conn, demo["p4"], "other_see_note", "Synthetic: looked fine at first.",
                                demo["staff"][0])
    second = resolutions.correct(conn, demo["p4"], first, "duplicate_ledger_entry_reversed",
                                 "Synthetic: LE-000527 is a retried write; reversed in the ledger.",
                                 demo["staff"][2])
    chain = resolutions.history(conn, demo["p4"])
    assert [(r.resolution_id, r.supersedes_id, r.in_force) for r in chain] == [
        (first, None, False), (second, first, True)]
    assert chain[0].note == "Synthetic: looked fine at first."
    item = resolutions.queue_item(conn, demo["p4"])
    assert (item.current_resolution_id, item.resolved_by, item.resolution_count) == (
        second, "Demo Controller", 2)


def test_a_chain_of_corrections_is_returned_oldest_first(conn, demo):
    ids = [resolutions.resolve(conn, demo["p5"], "other_see_note", "Synthetic: investigating sender.",
                               demo["staff"][0])]
    for note in ("Synthetic: sender identified as a customer.", "Synthetic: funds posted to account."):
        ids.append(resolutions.correct(conn, demo["p5"], ids[-1], "funds_identified_and_posted", note,
                                       demo["staff"][1]))
    chain = resolutions.history(conn, demo["p5"])
    assert [r.resolution_id for r in chain] == ids
    assert [r.in_force for r in chain] == [False, False, True]


# --- refusals, each in plain words ------------------------------------------------------------

def test_resolving_twice_is_refused_and_points_to_correcting(conn, demo):
    first = resolutions.resolve(conn, demo["p2"], "processor_error_claim_raised", NOTE, demo["staff"][0])
    with pytest.raises(resolutions.AlreadyResolved,
                       match=f"already resolved \\(resolution #{first} by Demo Analyst 1\\).*correct"):
        resolutions.resolve(conn, demo["p2"], "other_see_note", NOTE, demo["staff"][1])


def test_correcting_a_superseded_resolution_is_refused_as_stale(conn, demo):
    first = resolutions.resolve(conn, demo["p2"], "other_see_note", NOTE, demo["staff"][0])
    second = resolutions.correct(conn, demo["p2"], first, "processor_error_claim_raised", NOTE,
                                 demo["staff"][1])
    with pytest.raises(resolutions.StaleCorrection, match=f"in force is #{second} by Demo Analyst 2"):
        resolutions.correct(conn, demo["p2"], first, "bank_error_raised", NOTE, demo["staff"][0])


def test_correcting_with_another_exceptions_resolution_is_refused(conn, demo):
    other = resolutions.resolve(conn, demo["p5"], "other_see_note", NOTE, demo["staff"][0])
    resolutions.resolve(conn, demo["p2"], "other_see_note", NOTE, demo["staff"][0])
    with pytest.raises(resolutions.ResolutionRefused, match="does not belong to exception"):
        resolutions.correct(conn, demo["p2"], other, "other_see_note", NOTE, demo["staff"][0])


def test_a_correction_must_name_what_it_supersedes(conn, demo):
    with pytest.raises(resolutions.ResolutionRefused, match="must name the resolution"):
        resolutions.correct(conn, demo["p2"], None, "other_see_note", NOTE, demo["staff"][0])


@pytest.mark.parametrize("note", ["", "   ", "ok", "123456789"])
def test_short_or_blank_note_is_refused(conn, demo, note):
    with pytest.raises(resolutions.ResolutionRefused, match="at least 10 characters"):
        resolutions.resolve(conn, demo["p2"], "other_see_note", note, demo["staff"][0])


def test_unknown_reason_code_is_refused_listing_the_valid_ones(conn, demo):
    with pytest.raises(resolutions.ResolutionRefused,
                       match="unknown reason code 'looks_fine'; valid codes: bank_error_raised, "):
        resolutions.resolve(conn, demo["p2"], "looks_fine", NOTE, demo["staff"][0])


def test_unknown_staff_is_refused(conn, demo):
    with pytest.raises(resolutions.ResolutionRefused, match="unknown staff user"):
        resolutions.resolve(conn, demo["p2"], "other_see_note", NOTE, 987654321)


def test_unknown_exception_is_refused_as_unknown_not_as_unfinished(conn, demo):
    with pytest.raises(resolutions.ResolutionRefused, match="no exception with id 987654321"):
        resolutions.resolve(conn, 987654321, "other_see_note", NOTE, demo["staff"][0])


def test_exception_of_an_unfinished_run_is_refused(conn, build):
    month = build.standard_month()
    run_id = build.run(month.ledger_file, month.settlement_file, month.bank_file)
    exception_id = build.exception(run_id, 1, "timing")
    with pytest.raises(resolutions.ResolutionRefused, match="has not finished"):
        resolutions.resolve(conn, exception_id, "other_see_note", NOTE, month.staff)


def test_unrecognised_database_errors_are_not_disguised(conn, demo):
    with pytest.raises(psycopg.Error) as info:
        resolutions.resolve(conn, demo["p2"], "other_see_note", NOTE, "not-a-number")
    assert not isinstance(info.value, resolutions.ResolutionRefused)


def test_refusals_leave_the_connection_usable_and_nothing_written(conn, demo):
    with pytest.raises(resolutions.ResolutionRefused):
        resolutions.resolve(conn, demo["p2"], "other_see_note", "short", demo["staff"][0])
    assert resolutions.queue_item(conn, demo["p2"]).resolution_count == 0


# --- concurrency: two real connections, the database decides ------------------------------------

@pytest.fixture(scope="module")
def committed_demo():
    """A demo run committed in its own database, for tests that need two real connections."""
    with fresh_database() as url:
        migrate(url)
        with psycopg.connect(url, autocommit=True) as conn:
            run_id, staff = _load_demo_run(conn)
            exceptions = [i.exception_id for i in resolutions.queue(conn, run_id)]
        yield url, staff, exceptions


def _wait_until_blocked(observer: psycopg.Connection, pid: int) -> None:
    for _ in range(200):
        row = observer.execute(
            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", (pid,)).fetchone()
        if row and row[0] == "Lock":
            return
        time.sleep(0.05)
    raise AssertionError("the second writer never blocked on the first")


def _race(url: str, first_write, second_write) -> object:
    """Run first_write in an open transaction, start second_write on another connection, wait
    until the second is blocked on the first's lock, commit the first, return the second's result
    (or the exception it raised)."""
    outcome: dict[str, object] = {}
    with psycopg.connect(url) as first, psycopg.connect(url, autocommit=True) as second, \
            psycopg.connect(url, autocommit=True) as observer:
        first.execute("SELECT 1")          # open the transaction that will hold the lock
        first_write(first)

        def contender():
            try:
                outcome["value"] = second_write(second)
            except Exception as exc:       # noqa: BLE001 - the result under test
                outcome["value"] = exc

        thread = threading.Thread(target=contender)
        thread.start()
        _wait_until_blocked(observer, second.info.backend_pid)
        first.commit()
        thread.join(timeout=30)
        assert not thread.is_alive()
    return outcome["value"]


def test_two_people_resolving_the_same_exception_at_once_one_wins(committed_demo):
    url, staff, exceptions = committed_demo
    target = exceptions[0]
    result = _race(
        url,
        lambda c: resolutions.resolve(c, target, "other_see_note", "Synthetic: analyst one's note.", staff[0]),
        lambda c: resolutions.resolve(c, target, "other_see_note", "Synthetic: analyst two's note.", staff[1]),
    )
    assert isinstance(result, resolutions.AlreadyResolved)
    assert "by Demo Analyst 1" in str(result)
    with psycopg.connect(url) as conn:
        assert [r.note for r in resolutions.history(conn, target)] == ["Synthetic: analyst one's note."]


def test_two_people_correcting_the_same_resolution_at_once_one_wins(committed_demo):
    url, staff, exceptions = committed_demo
    target = exceptions[1]
    with psycopg.connect(url, autocommit=True) as conn:
        base = resolutions.resolve(conn, target, "other_see_note", "Synthetic: first look.", staff[0])
    result = _race(
        url,
        lambda c: resolutions.correct(c, target, base, "bank_error_raised", "Synthetic: correction A.", staff[0]),
        lambda c: resolutions.correct(c, target, base, "funds_returned_to_sender", "Synthetic: correction B.", staff[1]),
    )
    assert isinstance(result, resolutions.StaleCorrection)
    with psycopg.connect(url) as conn:
        chain = resolutions.history(conn, target)
    assert [r.note for r in chain] == ["Synthetic: first look.", "Synthetic: correction A."]


# --- command line -----------------------------------------------------------------------------

def test_cli_queue_show_resolve_correct(committed_demo, capsys):
    from recon.__main__ import main

    url, _, exceptions = committed_demo
    target = exceptions[2]
    base = ["--database-url", url]
    assert main(["queue", *base, "--run", "1", "--status", "open"]) == 0
    assert "OPEN" in capsys.readouterr().out

    assert main(["resolve", *base, "--exception", str(target), "--reason", "other_see_note",
                 "--note", "short"]) == 1
    assert "at least 10 characters" in capsys.readouterr().err

    assert main(["resolve", *base, "--exception", str(target), "--reason", "other_see_note",
                 "--note", "Synthetic: checked against the processor portal."]) == 0
    resolution_id = int(capsys.readouterr().out.split("#")[1].split()[0])

    assert main(["correct", *base, "--exception", str(target), "--supersedes", str(resolution_id),
                 "--reason", "funds_identified_and_posted", "--staff", "Demo Controller",
                 "--note", "Synthetic: funds identified and posted on 2 Oct."]) == 0
    capsys.readouterr()

    assert main(["show", *base, "--exception", str(target)]) == 0
    out = capsys.readouterr().out
    assert "superseded: other_see_note by Demo Analyst 1" in out
    assert "IN FORCE: funds_identified_and_posted by Demo Controller" in out
    assert "evidence:" in out

    assert main(["reasons", *base]) == 0
    assert "written_off_below_threshold" in capsys.readouterr().out
