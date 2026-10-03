# Mutation check for the matching engine

A reviewer's tool for answering one question: *would the tests notice if a matching rule were
wrong?* It breaks one rule at a time in `worker/recon/engine.py`, runs the full test suite
against each broken copy, and reports which tests failed. A mutation that no test notices is
reported as **SURVIVED**, which means a test is missing. Background and the recorded results
are in decision D-064 (`docs/decisions.md`).

It is deliberately **not part of CI**: it runs the whole suite once per mutation, about
30 minutes for all 18 locally. CI runs the suite once; this tool asks whether that suite would
catch a broken rule.

## How to run

You need the same disposable PostgreSQL server the tests use (see the main README,
"Running the tests").

```sh
# from the repository root
export RECON_TEST_ADMIN_URL="postgresql://postgres:recon_test@127.0.0.1:54329/postgres"
worker/.venv/Scripts/python tools/mutation_check/mutate_engine.py            # all 18
worker/.venv/Scripts/python tools/mutation_check/mutate_engine.py M01 M07    # chosen ones
```

The tool copies the repository into a temporary directory and mutates only that copy. The
working tree is never modified, and the copy is deleted at the end.

## Expected result

```
baseline: 367 passed ...
M01 gross_net: 1-cent tolerance: 3 failed, 364 passed ...
    test_gross_net_any_fee_difference_is_an_amount_mismatch[-1-EUR 0.01]
    ...
{"mutations": 18, "survived": []}
```

**Every mutation caught, exit code 0.** Other exit codes:

| Exit | Meaning |
|---|---|
| 1 | a mutation survived (a missing test), or the unmutated baseline failed |
| 2 | setup problem: no test database configured, an unknown mutation id, or a mutation whose anchor no longer occurs exactly once in `engine.py` (the engine changed; update the mutation) |

## The mutations

Each one is an exact textual replacement in `engine.py` that must occur exactly once, so a
mutation can never silently apply somewhere unintended.

| Id | What it breaks | Tests expected to fail |
|---|---|---|
| M01 | gross_net accepts a fee difference of up to 1 cent | fee off by −1 / +1 cent; persisted near-miss "fee 1 cent short" (refused by the database) |
| M02 | many_to_one matches a batch even when a line has no ledger entry | all-or-nothing batch; persisted near-miss "partial batch" |
| M03 | exact ignores the 0..5-day date window | outside-window timing; bank before ledger date; deadline sweep |
| M04 | a duplicate ledger entry is labelled `missing_from_bank` | demo P4; demo exception set; duplicate tests |
| M05 | payout timing reverts to `payout_date > period_to` (pre-D-048) | timing tests (a) and (b); deadline sweep |
| M06 | the deadline is dropped from payout timing explanations | (a); (b); demo deadlines; deadline sweep |
| M07 | exact ignores the amount | transfer 1 cent short; persisted near-miss |
| M08 | ledger-vs-processor date window widened from ±1 to ±2 days | ledger two days off |
| M09 | a debit is accepted as a payout credit | payout must be money in; persisted near-miss |
| M10 | payout credits are accepted on any date | payout window cases; deadline sweep |
| M11 | the tie-break takes the last candidate instead of the first | earliest of two identical credits |
| M12 | `unknown_deposit` and `unexplained_debit` are swapped | leftover-labelling tests; demo P5 |
| M13 | many_to_one no longer requires deposit = Σ net | batch 1 cent short; persisted near-miss |
| M14 | exact ignores the reference | different reference; persisted near-miss |
| M15 | ledger order reversed (changes which twin is matched) | demo P4; duplicate tests |
| M16 | ledger timing boundary uses `>=` instead of `>` | transfer and card boundary cases |
| M17 | conflicting payout dates are no longer refused | engine refusal; persisted failed run |
| M18 | the duplicate check ignores the amount | same reference, different amount |

When the engine changes, an anchor may stop matching (exit 2). Update the mutation to break
the same rule in the new code, and keep the list covering every matching rule.
