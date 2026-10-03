# Payment Reconciliation Engine (Level 1)

> **Synthetic demo data only.** Every file, row and screen in this project is synthetic.
> "Orrery Payments" (the payment processor) and "Demo Bank" are fictional. No real people,
> accounts, IBANs or transactions are used anywhere.

A reconciliation engine that matches a fintech's internal ledger, its payment processor's
settlement report, and its bank statement. It puts only the genuine differences in front of
a human, and can recompute any past reconciliation byte for byte.

- Design: [docs/design.md](docs/design.md)
- Every design decision, with the alternative rejected: [docs/decisions.md](docs/decisions.md)
- Source files the bank-format parser is built from: [docs/sources/](docs/sources/)

## Bank statement format

The bank statement format is **ISO 20022 camt.053.001.02** (BankToCustomerStatementV02). It was
chosen because both the official ISO 20022 XSD and an official ISO sample instance are
available for it (the XSD's SHA-256 was independently confirmed against the ISO 20022 message
archive), and because the published national implementation guides used as sources (Dutch
Payments Association; Finance Finland) describe this version. Newer camt.053 versions are
additive: the parser dispatches on the XML namespace, so supporting one means adding its XSD
source and a parser module, not changing the existing one. See decision D-002.

## Money

Amounts are stored as integer minor units (euro cents) with an explicit ISO 4217 currency code
on every amount, never as floating point. Binary floats cannot represent 0.10 exactly, so
summing payments in floats creates tiny phantom differences in exactly the place this system
exists to find real ones. See design §3 and decision D-001. Level 1 is EUR only.

## Repository layout

```
db/migrations/      numbered raw-SQL migrations (no ORM)
worker/recon/       Python worker: migration runner, synthetic generator, parsers, importer, engine, runs
worker/tests/       tests, run against real PostgreSQL
demo-data/2026-09/  the synthetic demo month (generated, committed, byte-reproducible)
docs/               design, decision log, sources
```

## The synthetic demo month and its planted problems

`demo-data/2026-09/` holds one synthetic month (September 2026), generated with

```sh
cd worker
.venv/Scripts/python -m recon generate --seed 20260901 --month 2026-09 --out ../demo-data/2026-09
```

The same seed and month always produce byte-identical files, and a test regenerates them and
compares bytes. The month contains 606 card payments through the fictional processor Orrery
Payments, paid out daily on T+2 with weekend roll-forward. It also has 6 refunds netted inside
payouts and 40 direct bank transfers: 653 ledger rows, 612 settlement lines, 30 payouts and
67 bank entries. Orrery's fictional fee is 1.4% (rounded half-up to the cent) plus EUR 0.25.

Five problems are planted deliberately, and the demo must catch every one.
`planted.json` lists the exact rows involved in each, and the outcome expected from the engine:

| # | Planted problem | What is in the files | Expected outcome |
|---|---|---|---|
| P1 | **Missing payout** | Payout PO-20260914 (9 payments, net EUR 1,752.23, dated 2026-09-16) is in the settlement report but never reaches the bank | exception `missing_from_bank` covering its 9 settlement and 9 ledger rows |
| P2 | **Fee mismatch** | Single-payment payout PO-20260913: gross EUR 316.85, stated fee 4.69, net 312.16; the bank received 311.76, EUR 0.40 less | exception `amount_mismatch` (ledger, settlement and bank row) |
| P3 | **50-to-1 batch deposit** | 50 card payments on 2026-09-16 paid out as one bank credit of EUR 10,624.87 (payout PO-20260916) | one `many_to_one` match citing 50 ledger + 50 settlement + 1 bank row |
| P4 | **Duplicate ledger entry** | Payment PAY-000520 (EUR 371.21) recorded twice: LE-000526 and LE-000527 | LE-000526 matched normally; LE-000527 becomes exception `possible_duplicate` |
| P5 | **Deposit nobody expected** | EUR 250.00 credit on 2026-09-22, remittance text "SYNTHETIC UNREFERENCED TRANSFER", no counterpart anywhere | exception `unknown_deposit` |

These are **expected timing differences, not problems**. They are listed so that nobody
mistakes them for planted errors:

| # | What | Expected outcome |
|---|---|---|
| T1 | Payout PO-20260929 (card payments of 29 Sep) is dated 1 Oct, after the statement period | exception `timing` |
| T2 | Payout PO-20260930 (card payments of 30 Sep) is dated 2 Oct | exception `timing` |
| T3 | Bank transfer PAY-000625, booked in the ledger on 30 Sep, reaches the bank in October | exception `timing` |

Every other row in the month reconciles. A test re-derives every disagreement between the
three files without reading `planted.json`, and requires the result to be exactly the items above.

## Status

| Stage | State |
|---|---|
| 1. Schema | done: migrations 0001–0006, migration runner, constraint tests |
| 2. Generator | done: synthetic month, planted problems, official-XSD validation |
| 3. Import | done: CSV and camt.053 parsers, idempotent import, parse jobs, `recon import` / `recon worker` |
| 4. Matching passes | done: exact, gross_net, many_to_one, classification with timing deadlines; runs persisted and re-verified by the database |
| 5. Exceptions queue | not started |
| 6. Replay test | not started |
| 7. Web UI | not started |

## Running the tests

The tests need a disposable PostgreSQL server; each session creates and drops its own
database. If no server is configured, the database tests **fail** rather than skip (D-036).

```sh
docker run -d --name recon-pg-test -e POSTGRES_PASSWORD=recon_test \
  -p 127.0.0.1:54329:5432 postgres:18-alpine

cd worker
python -m venv .venv
.venv/Scripts/python -m pip install "psycopg[binary]==3.3.6" "lxml==6.1.3" "pytest==9.1.1"   # Windows path
export RECON_TEST_ADMIN_URL="postgresql://postgres:recon_test@127.0.0.1:54329/postgres"
.venv/Scripts/python -m pytest
```

## Applying migrations

```sh
cd worker
DATABASE_URL="postgresql://..." .venv/Scripts/python -m recon migrate
```

The runner refuses to run if an applied migration was edited or removed, or if the migrations
directory contains anything unexpected (D-035).

## Importing files

```sh
cd worker
export DATABASE_URL="postgresql://..."
.venv/Scripts/python -m recon seed-staff            # synthetic demo staff (D-054)
.venv/Scripts/python -m recon import --kind ledger     --file ../demo-data/2026-09/synthetic_ledger_2026-09.csv
.venv/Scripts/python -m recon import --kind settlement --file ../demo-data/2026-09/synthetic_orrery_settlement_2026-09.csv
.venv/Scripts/python -m recon import --kind bank       --file ../demo-data/2026-09/synthetic_bank_camt053_2026-09.xml
.venv/Scripts/python -m recon worker                # processes parse jobs queued by the web upload
```

Importing the same bytes again does nothing and reports the existing file. A file the parser
rejects is kept as evidence, and its rejection reason is recorded; it can never be used in a
reconciliation run.

## Reconciling

```sh
cd worker
.venv/Scripts/python -m recon reconcile --ledger-file 1 --settlement-file 2 --bank-file 3
# run #1 finished: 65 matches, 7 exceptions, result sha256 ...
```

On the demo month the engine makes 65 matches (39 `exact`, 3 `gross_net`, 23 `many_to_one`,
including the 50-to-1 deposit P3) and raises 7 exceptions: P1, P2, P4 and P5 with their expected
reasons, and the three timing items T1–T3. Every timing exception states its deadline (D-048).
The run is written to the database, which re-checks every match's arithmetic before accepting
it as finished (D-012).
