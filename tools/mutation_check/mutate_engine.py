"""Mutation check for the matching engine (decision D-064).

Breaks one matching rule at a time in a throwaway copy of this repository, runs the full test
suite against each broken copy, and reports which tests failed. A rule whose mutation breaks
no test is reported as SURVIVED: that is a missing test.

Usage (from the repository root, with the worker's virtual environment):

    export RECON_TEST_ADMIN_URL=postgresql://postgres:...@127.0.0.1:54329/postgres
    worker/.venv/Scripts/python tools/mutation_check/mutate_engine.py           # all 18
    worker/.venv/Scripts/python tools/mutation_check/mutate_engine.py M01 M07   # chosen ones

Expected result: every mutation is caught (exit code 0). Exit code 1 means a mutation
survived or the unmutated baseline failed; exit code 2 means a mutation's anchor no longer
matches engine.py exactly once (the engine changed, so update the mutation).

The original repository is never modified: the copy lives in a temporary directory that is
deleted afterwards. Runtime is roughly one full test run per mutation plus a baseline
(about 30 minutes for all 18 locally), which is why this is a reviewer's tool, not a CI gate.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENGINE = Path("worker") / "recon" / "engine.py"

# name -> list of (exact text in engine.py, replacement). Each anchor must occur exactly once.
MUTATIONS = {
    "M01 gross_net: 1-cent tolerance": [(
        "            if row.amount_minor - entry.amount_minor != line.fee_minor:\n",
        "            if abs(row.amount_minor - entry.amount_minor - line.fee_minor) > 1:\n")],
    "M02 many_to_one: accept a partial batch": [
        ("                if row is None:\n                    break\n                chosen.append(row)\n"
         "            if len(chosen) != len(payout.lines):\n                continue\n",
         "                if row is None:\n                    continue\n                chosen.append(row)\n"
         "            if not chosen:\n                continue\n"),
        ("            if sum(row.amount_minor for row in chosen) != gross:\n                continue\n", ""),
    ],
    "M03 exact: drop the date window": [(
        "                and row.booked_on <= e.booking_date <= latest\n",
        "                and True\n")],
    "M04 duplicate mislabelled as missing_from_bank": [(
        'self._exception("possible_duplicate"', 'self._exception("missing_from_bank"')],
    "M05 timing: revert to the old rule (payout_date > period_to)": [(
        "            if entry is None:\n                if deadline > period_to:\n",
        "            if entry is None:\n                if payout.payout_date > period_to:\n")],
    "M06 timing: drop the deadline from the payout explanation": [(
        '                        f"{subject}: payout window {_days(PAYOUT_WINDOW_DAYS)}; expected at the bank "\n'
        '                        f"by {_day(deadline)}; this statement ends {_day(period_to)}. If not booked "\n'
        '                        f"by {_day(deadline)}, treat as missing."\n',
        '                        f"{subject}: payout window {_days(PAYOUT_WINDOW_DAYS)}; this statement ends "\n'
        '                        f"{_day(period_to)}."\n')],
    "M07 exact: ignore the amount": [(
        "                and e.amount_minor == row.amount_minor\n", "                and True\n")],
    "M08 ledger vs processor date window widened to 2 days": [(
        "            if abs((row.booked_on - line.created_on).days) > LEDGER_SETTLEMENT_WINDOW_DAYS:\n"
        "                continue\n            return row\n",
        "            if abs((row.booked_on - line.created_on).days) > LEDGER_SETTLEMENT_WINDOW_DAYS + 1:\n"
        "                continue\n            return row\n")],
    "M09 payout credit: accept debits": [(
        "            e.amount_minor > 0\n            and _bank_ref(e.end_to_end_id) == payout.reference\n",
        "            _bank_ref(e.end_to_end_id) == payout.reference\n")],
    "M10 payout credit: drop the payout window": [(
        "            and payout.payout_date <= e.booking_date <= latest\n", "            and True\n")],
    "M11 tie-break: take the last candidate instead of the first": [(
        "        for entry in self.bank:\n            if entry.entry_index in self.used_bank:\n"
        "                continue\n            if accept(entry):\n",
        "        for entry in reversed(self.bank):\n            if entry.entry_index in self.used_bank:\n"
        "                continue\n            if accept(entry):\n")],
    "M12 unknown_deposit / unexplained_debit swapped": [(
        '            if entry.amount_minor > 0:\n                self._exception("unknown_deposit"',
        '            if entry.amount_minor < 0:\n                self._exception("unknown_deposit"')],
    "M13 many_to_one: drop the deposit == sum(net) check": [(
        "            if entry.amount_minor != net:\n                continue\n"
        "            if sum(row.amount_minor for row in chosen) != gross:\n",
        "            if sum(row.amount_minor for row in chosen) != gross:\n")],
    "M14 exact: drop the reference check": [(
        "                _bank_ref(e.remittance_ustrd) == row.payment_reference\n"
        "                and e.amount_minor == row.amount_minor\n",
        "                True\n                and e.amount_minor == row.amount_minor\n")],
    "M15 ledger order reversed (which twin is matched)": [(
        "        self.ledger = sorted(ledger, key=lambda r: (r.booked_on, r.entry_id, r.row_number))\n",
        "        self.ledger = sorted(ledger, key=lambda r: (r.booked_on, r.entry_id, r.row_number), reverse=True)\n")],
    "M16 ledger timing boundary off by one (>=)": [(
        "            if deadline > period_to:\n                self._exception(\"timing\", [row]",
        "            if deadline >= period_to:\n                self._exception(\"timing\", [row]")],
    "M17 inconsistent payout dates no longer refused": [(
        "            if len(dates) != 1 or len(ids) != 1:\n", "            if False:\n")],
    "M18 duplicate test ignores the amount": [(
        "                             and o.amount_minor == row.amount_minor\n", "")],
}


def run_suite(copy: Path) -> tuple[list[str], str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider"],
        cwd=copy / "worker", capture_output=True, text=True, timeout=3600,
    )
    # Both outcomes mean the suite did not pass: an assertion failed, or setup broke.
    failed = sorted({f"[failed] {t}" for t in re.findall(r"^FAILED (\S+)", proc.stdout, re.M)}
                    | {f"[error] {t}" for t in re.findall(r"^ERROR (\S+)", proc.stdout, re.M)})
    lines = proc.stdout.strip().splitlines()
    summary = lines[-1] if lines else proc.stderr.strip()[-500:]
    if proc.returncode not in (0, 1):   # 0 all passed, 1 some failed; anything else is an error
        failed = failed or [f"pytest exited {proc.returncode}: {summary}"]
    return failed, summary


def main(selected: list[str]) -> int:
    if not os.environ.get("RECON_TEST_ADMIN_URL"):
        print("RECON_TEST_ADMIN_URL is not set; the test suite needs a disposable PostgreSQL "
              "server (see README.md, 'Running the tests').", file=sys.stderr)
        return 2
    unknown = [s for s in selected if s not in {name.split()[0] for name in MUTATIONS}]
    if unknown:
        print(f"unknown mutation id(s): {unknown}", file=sys.stderr)
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="recon-mutation-"))
    try:
        copy = workdir / "repo"
        shutil.copytree(REPO, copy, ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", ".pytest_cache", "node_modules"))
        engine = copy / ENGINE
        original = engine.read_text(encoding="utf-8")

        baseline, summary = run_suite(copy)
        print(f"baseline: {summary}", flush=True)
        if baseline:
            print("the unmutated baseline does not pass; fix that first", file=sys.stderr)
            return 1

        report: dict[str, list[str]] = {}
        for name, edits in MUTATIONS.items():
            if selected and name.split()[0] not in selected:
                continue
            mutated = original
            for old, new in edits:
                count = mutated.count(old)
                if count != 1:
                    print(f"{name}: anchor occurs {count} times in engine.py, expected 1; "
                          "update this mutation:", file=sys.stderr)
                    print(old, file=sys.stderr)
                    return 2
                mutated = mutated.replace(old, new)
            engine.write_text(mutated, encoding="utf-8")
            try:
                failed, summary = run_suite(copy)
            finally:
                engine.write_text(original, encoding="utf-8")
            report[name] = failed
            print(f"{name}: {summary}" + ("" if failed else "   <-- SURVIVED"), flush=True)
            for test in failed:
                marker, name = test.split(" ", 1)
                print(f"    {marker} {name.split('::', 1)[-1]}", flush=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    survived = [name for name, failed in report.items() if not failed]
    print(json.dumps({"mutations": len(report), "survived": survived}), flush=True)
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
