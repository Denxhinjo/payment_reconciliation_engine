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
worker/recon/       Python worker: migration runner (stage 1); parsers, engine, generator later
worker/tests/       tests, run against real PostgreSQL
docs/               design, decision log, sources
```

## Status

| Stage | State |
|---|---|
| 1. Schema | done: migrations 0001–0006, migration runner, constraint tests |
| 2. Generator | not started (the planted-problem list will be added here) |
| 3. Import | not started |
| 4. Matching passes | not started |
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
.venv/Scripts/python -m pip install "psycopg[binary]==3.3.6" "pytest==9.1.1"   # Windows path
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
