# Payment Reconciliation Engine: Level 1 Design

Status: **APPROVED** by the project owner on 2026-10-02, including D-002, D-018, D-020,
D-030 and D-031. Changes after approval are recorded in `docs/decisions.md`.
Every data file this project produces is **synthetic demo data**. The payment processor
("Orrery Payments") and the bank ("Demo Bank") are fictional.

Companion documents:
- `docs/decisions.md`: one entry per design decision, with the rejected alternative.
- `docs/sources/`: the source files the bank-format parser is built from, unmodified.

---

## 1. What the system does

A fintech's money movements show up in three records that should agree:

| Record | Owner | Format in this demo | What it says |
|---|---|---|---|
| Internal ledger | the fintech | CSV (internal export) | what the app believes happened, at **gross** amounts |
| Settlement report | Orrery Payments (fictional processor) | CSV | what the processor collected, its fee, the **net**, and which **payout** each item went into |
| Bank statement | Demo Bank (fictional) | ISO 20022 **camt.053.001.02** XML | what actually arrived in the account |

The engine reads the three files, matches them in three ordered passes, and puts everything
left over into an exceptions queue with a machine-suggested reason. A staff member resolves
each exception with a reason code and a written note, and resolutions can never be edited.

The headline property is that **a reconciliation run can be recomputed byte for byte**
from the stored raw files and the engine version. A month that gets disputed later can be
reproduced exactly, not approximately.

---

## 2. Architecture

```
            upload (raw bytes)                      GitHub Actions
 browser ──────────────────────► Next.js (Vercel) ──── cron + workflow_dispatch ───┐
                                    │  raw SQL (pg)                                 │
                                    ▼                                               ▼
                             PostgreSQL (Neon) ◄──────── raw SQL (psycopg) ── Python worker
                             import_file (raw)                                 recon.parse
                             parsed rows                                       recon.engine  (pure)
                             runs / matches / exceptions / allocations         recon.persist
                             resolutions (append-only)                         recon.generate
```

- **Web (Next.js + TypeScript, `web/`)** handles upload, run list, run detail, exceptions
  queue and the resolve form. It writes only `import_file`, `job` and `resolution`, and never
  computes a match.
- **Worker (Python, `worker/`, package `recon`)** contains the parsers, the matching engine,
  the persistence layer, the synthetic data generator and the migration runner. GitHub Actions
  runs it on a cron schedule, and the UI's "Run now" button triggers it with
  `workflow_dispatch`. Jobs are claimed from a `job` table with
  `FOR UPDATE SKIP LOCKED`.
- **Database (PostgreSQL on Neon free tier)** uses numbered raw SQL migrations in
  `db/migrations/NNNN_name.sql`. The runner records each applied file's SHA-256 and refuses to
  start if an already-applied migration has been edited.
- **Dependencies, kept to what Level 1 needs.** Python: `psycopg` (database), `lxml`
  (XSD validation), `pytest` (dev). Node: `next`, `react`, `pg`. No ORM, queue service, object
  storage or paid service.

### 2.1 The engine is a pure function of raw bytes

```
reconcile(ledger_bytes, settlement_bytes, bank_bytes, ENGINE_VERSION) -> canonical_result_bytes
```

The engine parses the raw files itself and returns one canonical byte string (section 6).
The database tables of matches, exceptions and allocations are a **projection** of that
result for querying and enforcement. They are never the engine's input. Replay therefore
reloads the raw files from `import_file.raw`, calls `reconcile`, and compares SHA-256 hashes.

---

## 3. Money

- Every amount is a **`bigint` count of minor units** (euro cents) and sits next to an
  explicit ISO 4217 `currency char(3)` column. No `float`, `real`, `double precision` or
  `numeric` amount columns exist anywhere in the schema.
- **Why not floats:** binary floating point cannot represent 0.10 exactly. Summing fifty card
  payments in floats can produce a total that differs from the deposit by 0.0000000001.
  That is a phantom difference in exactly the place this system exists to find real
  ones. A reconciliation tool that invents differences, or that hides them behind a
  rounding tolerance, cannot be trusted. Integer cents make every comparison exact equality.
- **Parsing:** decimal strings from the bank file (`105678.50`) are converted to minor units
  by string arithmetic only, with no float or Decimal round-trip. The XSD allows up to 5
  fraction digits (`fractionDigits=5`). For EUR, whose minor-unit exponent is 2 (ISO 4217
  list, source S6), any non-zero digit beyond the 2nd decimal place rejects the file.
- **Web:** `pg` returns `bigint` as a string, and the UI formats it with `BigInt` and integer
  division. `parseFloat` and `Number()` are never applied to an amount.
- **Level 1 is EUR only.** The worker fails a run (status `failed`, explicit error) if any
  input row is not EUR. No exchange rates exist anywhere in the system.

---

## 4. Database schema

Conventions: `bigint GENERATED ALWAYS AS IDENTITY` primary keys, `timestamptz` for wall-clock
times (used only outside the engine), and `date` for business dates. Each constraint below
states the reason it exists. The migrations will contain these definitions; minor syntax may
differ but no constraint will be dropped without a `decisions.md` entry.

### 4.1 `import_file`: raw uploads, immutable

```sql
CREATE TYPE file_kind AS ENUM ('ledger', 'settlement', 'bank');

CREATE TABLE import_file (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind          file_kind NOT NULL,
  original_name text NOT NULL,
  raw           bytea NOT NULL CHECK (octet_length(raw) BETWEEN 1 AND 4194304),
  sha256        bytea GENERATED ALWAYS AS (sha256(raw)) STORED,
  uploaded_by   bigint NOT NULL REFERENCES staff_user(id),
  uploaded_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT import_file_sha256_unique UNIQUE (sha256),
  CONSTRAINT import_file_id_kind UNIQUE (id, kind)
);
```

| Constraint | Reason |
|---|---|
| `sha256` is a **generated column** from `raw` | The hash cannot disagree with the bytes, because no code path computes it and every insert gets it. |
| `UNIQUE (sha256)` | **Idempotent import:** uploading the same file twice hits the constraint. The insert is `ON CONFLICT (sha256) DO NOTHING`, and the UI reports "already imported as file #N". No new rows or jobs are created. |
| `raw` stored as uploaded | The web layer reads the body as an `ArrayBuffer` and does no newline or encoding normalisation. A trigger rejects every `UPDATE` and `DELETE`, so the raw file is evidence. |
| `octet_length` 1 to 4 MiB | This stays under Vercel's request body limit and Neon free-tier storage. An empty file is never valid. |
| `UNIQUE (id, kind)` | This is the target of composite foreign keys, so a run can only point at a file of the right kind. |

`import_parse(import_file_id PK → import_file, parser_version, status ∈ {parsed, rejected},
error text, parsed_at)` records the parse outcome separately, so `import_file` never needs an
`UPDATE`.

### 4.2 Parsed input rows, append-only

Rows are written once by the parser. Triggers reject `UPDATE` and `DELETE` because runs
reference them. Each row keeps its **natural key**, `(import_file_id, row_number)`, which is
the identifier the engine output uses.

```sql
CREATE TABLE ledger_entry (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  import_file_id    bigint NOT NULL REFERENCES import_file(id),
  row_number        integer NOT NULL CHECK (row_number >= 1),
  entry_id          text NOT NULL,
  booked_on         date NOT NULL,
  entry_type        text NOT NULL CHECK (entry_type IN ('payment', 'refund')),
  channel           text NOT NULL CHECK (channel IN ('card', 'bank_transfer')),
  payment_reference text NOT NULL CHECK (payment_reference <> ''),
  amount_minor      bigint NOT NULL CHECK (amount_minor <> 0),
  currency          char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  description       text NOT NULL,
  CHECK ((entry_type = 'payment') = (amount_minor > 0)),
  UNIQUE (import_file_id, row_number),
  UNIQUE (import_file_id, entry_id),
  UNIQUE (id, import_file_id)
);
```
- The **sign convention** is that positive means money in to the company, and the CHECK ties
  the sign to the type. A refund recorded as +20.00 is rejected at import, not discovered at
  month end.
- `UNIQUE (import_file_id, entry_id)` rejects a file in which the ledger's own primary key
  repeats. The planted *duplicate ledger entry* is two **different** entry IDs for the same
  payment, which is the realistic bug (a retried write), and it must reach the engine.
- `UNIQUE (id, import_file_id)` is the composite-FK target that ties allocations to the
  run's own files (4.4).

```sql
CREATE TABLE settlement_line (
  id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  import_file_id         bigint NOT NULL REFERENCES import_file(id),
  row_number             integer NOT NULL CHECK (row_number >= 1),
  balance_transaction_id text NOT NULL,
  created_on             date NOT NULL,
  line_type              text NOT NULL CHECK (line_type IN ('charge', 'refund')),
  merchant_reference     text NOT NULL CHECK (merchant_reference <> ''),
  currency               char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  gross_minor            bigint NOT NULL CHECK (gross_minor <> 0),
  fee_minor              bigint NOT NULL CHECK (fee_minor >= 0),
  net_minor              bigint NOT NULL,
  payout_id              text NOT NULL,
  payout_reference       text NOT NULL,
  payout_date            date NOT NULL,
  CHECK (net_minor = gross_minor - fee_minor),
  CHECK ((line_type = 'charge') = (gross_minor > 0)),
  UNIQUE (import_file_id, row_number),
  UNIQUE (import_file_id, balance_transaction_id),
  UNIQUE (id, import_file_id)
);
```
- `net = gross − fee` is enforced on every row. A processor report that is inconsistent
  with itself is rejected at import: we do not reconcile against a document that disagrees
  with itself. The planted *fee mismatch* is a report that is internally consistent but
  disagrees with the **bank**, which is the realistic case.

```sql
CREATE TABLE bank_statement (               -- one per bank import_file (Level 1: one Stmt per file)
  import_file_id bigint PRIMARY KEY REFERENCES import_file(id),
  msg_id         text NOT NULL,             -- GrpHdr/MsgId
  stmt_id        text NOT NULL,             -- Stmt/Id
  account_id     text NOT NULL,             -- Stmt/Acct/Id/Othr/Id (synthetic, never an IBAN)
  currency       char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  period_from    date NOT NULL,             -- Stmt/FrToDt/FrDtTm (date part as stated)
  period_to      date NOT NULL,             -- Stmt/FrToDt/ToDtTm
  opening_minor  bigint NOT NULL,           -- Bal with Tp/CdOrPrtry/Cd = OPBD, signed by CdtDbtInd
  closing_minor  bigint NOT NULL,           -- Bal with Tp/CdOrPrtry/Cd = CLBD, signed by CdtDbtInd
  CHECK (period_to >= period_from)
);

CREATE TABLE bank_entry (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  import_file_id   bigint NOT NULL REFERENCES bank_statement(import_file_id),
  entry_index      integer NOT NULL CHECK (entry_index >= 1),   -- position of Ntry in document order
  amount_minor     bigint NOT NULL CHECK (amount_minor <> 0),   -- signed: CRDT > 0, DBIT < 0
  cdt_dbt_ind      text NOT NULL CHECK (cdt_dbt_ind IN ('CRDT', 'DBIT')),
  currency         char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  status           text NOT NULL CHECK (status = 'BOOK'),
  booking_date     date NOT NULL,
  value_date       date,
  acct_svcr_ref    text,
  ntry_ref         text,
  bank_tx_code     text NOT NULL,          -- 'PMNT/RCDT/ESCT' (Domn/Cd / Fmly/Cd / SubFmlyCd)
  end_to_end_id    text,                   -- NtryDtls/TxDtls/Refs/EndToEndId
  remittance_ustrd text,                   -- NtryDtls/TxDtls/RmtInf/Ustrd (joined if repeated)
  debtor_name      text,                   -- NtryDtls/TxDtls/RltdPties/Dbtr/Nm
  CHECK ((cdt_dbt_ind = 'CRDT') = (amount_minor > 0)),
  UNIQUE (import_file_id, entry_index),
  UNIQUE (id, import_file_id)
);
```
- `status = 'BOOK'`: Level 1 reconciles booked entries only. A file containing `PDNG` or
  `INFO` entries is rejected (fail closed), not silently filtered.
- The **balance tie-out** `opening + Σ entries = closing` is checked by the parser. A file
  that fails it is rejected, because a statement that does not add up is not evidence.

### 4.3 `reconciliation_run`: the versioned record

```sql
CREATE TABLE reconciliation_run (
  id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  engine_version     text NOT NULL CHECK (engine_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
  engine_git_sha     text,                                    -- provenance only, not in result
  ledger_file_id     bigint NOT NULL,
  ledger_kind        file_kind GENERATED ALWAYS AS ('ledger') STORED,
  settlement_file_id bigint NOT NULL,
  settlement_kind    file_kind GENERATED ALWAYS AS ('settlement') STORED,
  bank_file_id       bigint NOT NULL,
  bank_kind          file_kind GENERATED ALWAYS AS ('bank') STORED,
  replay_of_run_id   bigint REFERENCES reconciliation_run(id),
  status             text NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'finished', 'failed')),
  error              text,
  started_at         timestamptz NOT NULL DEFAULT now(),
  finished_at        timestamptz,
  result_canonical   bytea,
  result_sha256      bytea GENERATED ALWAYS AS (sha256(result_canonical)) STORED,
  FOREIGN KEY (ledger_file_id, ledger_kind)         REFERENCES import_file(id, kind),
  FOREIGN KEY (settlement_file_id, settlement_kind) REFERENCES import_file(id, kind),
  FOREIGN KEY (bank_file_id, bank_kind)             REFERENCES import_file(id, kind),
  CHECK ((status = 'finished') = (result_canonical IS NOT NULL)),
  CHECK ((status = 'running') = (finished_at IS NULL)),
  CHECK ((status = 'failed') = (error IS NOT NULL)),
  UNIQUE (id, ledger_file_id),
  UNIQUE (id, settlement_file_id),
  UNIQUE (id, bank_file_id)
);
```

| Constraint | Reason |
|---|---|
| Composite FK with a constant generated `*_kind` column | A run cannot be pointed at a ledger file in the bank slot. The database enforces it, not the code. |
| `result_sha256` generated from `result_canonical` | The stored hash is always the hash of the stored bytes. |
| `replay_of_run_id` | A replay is a new run linked to the original. A mismatching replay is **recorded, not refused**, because a mismatch is evidence and must not disappear. The UI shows "replay identical: yes/no" by comparing hashes. |
| `started_at` / `finished_at` | These are wall-clock times on the record of the run. They are deliberately outside `result_canonical` (section 6). |
| Status transitions (trigger) | The only permitted `UPDATE` is `running → finished` or `running → failed`, once. `DELETE` is rejected. |
| **Finish check (trigger on `running → finished`)** | This verifies inside the database that (1) every row of the three input files has exactly one allocation in this run, and (2) every match has the shape and arithmetic its pass claims (table below). That gives a second, independent implementation of the matching rules' invariants, in SQL, so a bug in the Python engine cannot produce a finished run that violates them. |

Finish-check arithmetic, per match:

| Pass | Shape (ledger / settlement / bank rows) | Arithmetic checked in SQL |
|---|---|---|
| `exact` | 1 / 0 / 1 | ledger amount = bank amount; reference equal |
| `gross_net` | 1 / 1 / 1 | ledger amount = settlement gross; bank amount = settlement net; ledger − bank = settlement fee |
| `many_to_one` | n ≥ 2 / n / 1 | Σ ledger = Σ settlement gross; bank = Σ settlement net; all settlement rows share one payout reference |

### 4.4 Matches, exceptions, and allocations: "consumed at most once" in the database

```sql
CREATE TABLE run_match (   -- named run_match, not match: see D-033
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id      bigint NOT NULL REFERENCES reconciliation_run(id),
  ordinal     integer NOT NULL CHECK (ordinal >= 1),          -- position in canonical result
  pass        text NOT NULL CHECK (pass IN ('exact', 'gross_net', 'many_to_one')),
  explanation text NOT NULL,
  UNIQUE (run_id, ordinal),
  UNIQUE (id, run_id)
);

CREATE TABLE run_exception (   -- see D-033
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id           bigint NOT NULL REFERENCES reconciliation_run(id),
  ordinal          integer NOT NULL CHECK (ordinal >= 1),
  suggested_reason text NOT NULL CHECK (suggested_reason IN (
                     'missing_from_bank', 'amount_mismatch', 'timing', 'unknown_deposit',
                     'possible_duplicate', 'missing_from_ledger', 'unexplained_debit')),
  explanation      text NOT NULL,
  fingerprint      bytea NOT NULL,   -- sha256(reason + sorted natural keys of its rows)
  UNIQUE (run_id, ordinal),
  UNIQUE (id, run_id)
);

-- One table per input kind (shown for ledger; settlement and bank are identical in shape).
CREATE TABLE allocation_ledger (
  run_id          bigint NOT NULL,
  ledger_file_id  bigint NOT NULL,
  ledger_entry_id bigint NOT NULL,
  match_id        bigint,
  exception_id    bigint,
  PRIMARY KEY (run_id, ledger_entry_id),
  CHECK (num_nonnulls(match_id, exception_id) = 1),
  FOREIGN KEY (run_id, ledger_file_id)          REFERENCES reconciliation_run(id, ledger_file_id),
  FOREIGN KEY (ledger_entry_id, ledger_file_id) REFERENCES ledger_entry(id, import_file_id),
  FOREIGN KEY (match_id, run_id)                REFERENCES run_match(id, run_id),
  FOREIGN KEY (exception_id, run_id)            REFERENCES run_exception(id, run_id)
);
```

| Constraint | Reason |
|---|---|
| `PRIMARY KEY (run_id, ledger_entry_id)` | **An input row is consumed by at most one match per run.** A second allocation of the same row is a primary-key violation. This holds per run, because a replay is a separate run that legitimately allocates the same rows again. |
| `num_nonnulls(match_id, exception_id) = 1` | A row is either matched or an exception, never both and never neither. Combined with the finish check this gives **exactly one** allocation per row. |
| `(run_id, ledger_file_id)` → `reconciliation_run(id, ledger_file_id)` and `(ledger_entry_id, ledger_file_id)` → `ledger_entry(id, import_file_id)` | The allocated row must come from **this run's** ledger file. A run cannot consume a row from another month's file. Both FKs share the `ledger_file_id` column, which makes the tie purely declarative. |
| `(match_id, run_id)` → `run_match(id, run_id)` | A row cannot be allocated to a match that belongs to a different run. |
| Immutability (trigger) | `INSERT` into `run_match`, `run_exception` and `allocation_*` is allowed only while the parent run is `running`. `UPDATE` and `DELETE` are always rejected. |

"Every match cites the input rows it consumed" is implemented as the set of `allocation_*`
rows pointing at it. It also appears in the canonical result as natural keys.

**Why three allocation tables instead of one polymorphic `match_item`:** each table gets real
foreign keys to its row table and to the file-kind slot on the run. A polymorphic
`(source_kind, row_id)` column cannot have a foreign key at all (see `decisions.md`).

### 4.5 Exceptions queue: resolutions, append-only

```sql
CREATE TABLE staff_user (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  display_name text NOT NULL UNIQUE,
  is_synthetic boolean NOT NULL DEFAULT true          -- demo accounts are labelled as such
);

CREATE TABLE resolution_reason (                      -- seeded by migration, not editable in UI
  code  text PRIMARY KEY,
  label text NOT NULL
);

CREATE TABLE resolution (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  exception_id  bigint NOT NULL REFERENCES run_exception(id),
  supersedes_id bigint,
  reason_code   text NOT NULL REFERENCES resolution_reason(code),
  note          text NOT NULL CHECK (char_length(btrim(note)) >= 10),
  resolved_by   bigint NOT NULL REFERENCES staff_user(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, exception_id),
  FOREIGN KEY (supersedes_id, exception_id) REFERENCES resolution(id, exception_id),
  CONSTRAINT resolution_superseded_once UNIQUE (supersedes_id)
);
CREATE UNIQUE INDEX resolution_one_root_per_exception
  ON resolution (exception_id) WHERE supersedes_id IS NULL;
```

| Constraint | Reason |
|---|---|
| Trigger: `BEFORE UPDATE OR DELETE` and `BEFORE TRUNCATE` raise | **Append-only.** A resolution is corrected by inserting a new one that supersedes it. The old one stays visible forever. |
| `REVOKE UPDATE, DELETE ON resolution FROM web_app` | Defence in depth: the web's database role lacks the privilege even before the trigger runs. (The table owner can still drop a trigger. This protects against application bugs and misuse through the app, not against a hostile DBA. See "Noticed, not done": hash chaining.) |
| `note` with at least 10 non-blank characters | **Mandatory written note.** A bare "ok" is rejected by the database, not just the form. |
| `(supersedes_id, exception_id)` FK | A correction must supersede a resolution of **the same** exception. |
| `UNIQUE (supersedes_id)` + partial unique root index | Each exception's history is a **single linear chain**: one first resolution, and each resolution corrected at most once. The current resolution is the chain's tail, so there is never ambiguity about which one is in force. |
| Trigger: exception's run must be `finished` | Exceptions from a running or failed run cannot be resolved. |

Seeded reason codes (proposed): `timing_clears_next_period`, `processor_error_claim_raised`,
`duplicate_ledger_entry_reversed`, `funds_identified_and_posted`, `funds_returned_to_sender`,
`bank_error_raised`, `written_off_below_threshold`, `other_see_note`.

### 4.6 `job`: operational queue (mutable on purpose)

`job(id, kind ∈ {parse_file, reconcile, replay}, import_file_id, run_id, status ∈
{queued, running, done, failed}, attempts, error, created_at, started_at, finished_at)`.
This is operational state, not evidence, so status updates are allowed. Nothing about a
reconciliation's outcome lives here.

### 4.7 Migration plan

| File | Contents |
|---|---|
| `0001_staff_and_files.sql` | `staff_user`, `file_kind`, `import_file`, `import_parse`, immutability triggers |
| `0002_input_rows.sql` | `ledger_entry`, `settlement_line`, `bank_statement`, `bank_entry`, append-only triggers |
| `0003_runs.sql` | `reconciliation_run`, `run_match`, `run_exception`, `allocation_*`, status and immutability triggers, finish check |
| `0004_resolutions.sql` | `resolution_reason` (+ seed), `resolution`, append-only triggers |
| `0005_jobs.sql` | `job` |
| `0006_roles.sql` | `web_app` and `worker` role grants (applied where the platform allows role creation) |

---

## 5. Matching algorithm

### 5.1 Input normalisation (inside the engine, from raw bytes)

| Source | Reference used for matching | Date used |
|---|---|---|
| Ledger | `payment_reference` | `booked_on` |
| Settlement | `merchant_reference` (to ledger); `payout_reference` (to bank) | `created_on` (to ledger); `payout_date` (to bank) |
| Bank, processor payout | `EndToEndId` (Orrery puts the payout reference here) | `BookgDt` |
| Bank, direct customer transfer | `RmtInf/Ustrd`, trimmed, compared exactly | `BookgDt` |

References are compared by **exact string equality after trimming surrounding whitespace**.
There is no fuzzy or substring matching, because a "probably the same" match is exactly what
a later dispute will question.

### 5.2 Configuration (part of the engine version and written into the result)

| Parameter | Value | Meaning |
|---|---|---|
| `EXACT_WINDOW_DAYS` | 5 | a direct transfer may arrive 0 to 5 calendar days after the ledger date |
| `LEDGER_SETTLEMENT_WINDOW_DAYS` | 1 | ledger date and processor `created_on` may differ by ±1 day (time-zone edge) |
| `PAYOUT_WINDOW_DAYS` | 3 | the bank may book a payout 0 to 3 calendar days after `payout_date` |

Windows are in **calendar days**. A banking-holiday calendar is reference data that would
need a cited source, so Level 1 does not use one, and the 3-day payout window absorbs
weekends.

### 5.3 Deterministic ordering (used everywhere a choice is made)

- Ledger: `(booked_on, entry_id, row_number)`
- Settlement: `(payout_date, payout_reference, created_on, balance_transaction_id, row_number)`
- Bank: `(booking_date, entry_index)`
- Payout groups: `(payout_date, payout_reference)`

Whenever several candidates satisfy a rule, the **first in this order** is taken. Every
candidate not taken stays unallocated and **always surfaces as an exception**. The stable
choice therefore decides only *which* of two identical rows is called the duplicate. It can
never hide one.

### 5.4 Pass A: `exact` (direct bank transfers)

For each unallocated ledger row with `channel = 'bank_transfer'`, in ledger order, take the
first unallocated bank entry where:
- `remittance_ustrd == payment_reference`,
- `amount_minor` is equal (signed) and currency is equal,
- `booked_on ≤ booking_date ≤ booked_on + EXACT_WINDOW_DAYS`.

The match consumes 1 ledger row and 1 bank entry. Example explanation:
*"Pass exact: ledger LE-000412 and bank entry #37 share reference PAY-000412, amount
EUR 120.00, bank booked 2 days after ledger."*

### 5.5 Pass B: `gross_net` (single-payment payouts)

For each payout group (settlement rows sharing `payout_reference`) with **exactly one row**,
in payout-group order:
1. Ledger: first unallocated row with `channel = 'card'`, `payment_reference ==
   merchant_reference`, `amount_minor == gross_minor`, and `|booked_on − created_on| ≤
   LEDGER_SETTLEMENT_WINDOW_DAYS`.
2. Bank: first unallocated `CRDT` entry with `end_to_end_id == payout_reference` and
   `payout_date ≤ booking_date ≤ payout_date + PAYOUT_WINDOW_DAYS`.
3. Accept only if `ledger.amount − bank.amount == settlement.fee`, which by the row CHECK is
   equivalent to `bank.amount == settlement.net`.

The match consumes 1 ledger, 1 settlement and 1 bank row. Example explanation: *"Pass
gross_net: ledger EUR 100.00 gross − bank EUR 98.35 = EUR 1.65, equal to the fee stated
on settlement line BT-… for payout PO-…"*

### 5.6 Pass C: `many_to_one` (batched payouts)

For each payout group with **two or more rows**, in payout-group order:
1. For **every** settlement row in the group (settlement order), find its ledger row as in
   pass B step 1. Ledger rows found earlier in this group are excluded, so one ledger row
   cannot satisfy two settlement rows.
2. Find the bank `CRDT` entry as in pass B step 2.
3. Accept only if **every** settlement row found a ledger row, `bank.amount == Σ net`, and
   `Σ ledger == Σ gross` (hence `Σ ledger − bank == Σ fee`).

Refund lines inside a batch carry negative gross and net values and are summed like any
other row. The match consumes n ledger, n settlement and 1 bank row.

**All or nothing per batch.** If one settlement row in a 50-row batch has no ledger
counterpart, the batch is not matched. It becomes one exception that names the orphan row.
Matching 49 of 50 and quietly netting the 50th would be the convenient, wrong answer.

**Why group by payout reference rather than search for subsets that sum to the deposit:**
subset-sum is computationally hard, and when several subsets fit, the choice between them is
a guess. The processor already states which payments it paid out together. The engine uses
that statement and verifies it against both the ledger and the bank.

### 5.7 Exception classification (after the three passes)

The remaining rows are grouped and classified in this fixed order. Each group becomes one
exception and every row lands in exactly one.

1. **Payout groups with unallocated settlement rows** (payout-group order). Gather the group's
   settlement rows, their ledger counterparts (pass B rule, unallocated only), and an
   unallocated bank entry with that `end_to_end_id` (any date).
   - The bank entry is found but sums differ → **`amount_mismatch`**. The explanation states
     gross, stated fee, deposit and the unexplained difference.
   - Every sum agrees but some settlement row has no ledger row → **`missing_from_ledger`**,
     naming the row.
   - No bank entry, and `payout_date > period_to` (statement end) → **`timing`**.
     *Superseded 2026-10-03 by D-048: the condition is now `payout_date + PAYOUT_WINDOW_DAYS > period_to`, and the explanation must state the deadline.*
   - No bank entry otherwise → **`missing_from_bank`**.
2. **Remaining direct-transfer ledger rows**: if an unallocated bank entry carries the same
   reference with a different amount → **`amount_mismatch`** (both rows). Otherwise the
   duplicate and timing rules below apply.
3. **Remaining ledger rows** (ledger order):
   - If another ledger row with the same reference and amount **was matched** →
     **`possible_duplicate`**. The explanation cites the matched entry and its match.
   - Else if `booked_on + window > period_to` (the money could not have arrived inside the
     statement period) → **`timing`**.
     *Refined 2026-10-03 by D-048: "window" is defined per row kind, and the explanation must state the deadline.*
   - Else → **`missing_from_bank`**.
4. **Remaining bank entries**: `CRDT` → **`unknown_deposit`**; `DBIT` → **`unexplained_debit`**.

The fingerprint is `sha256(reason ‖ sorted natural keys)`. It is stored now so that a later
level can carry resolutions across runs. Level 1 attaches resolutions to one run's exceptions.

> **Approved 2026-10-02 (D-018):** the brief lists four suggested reasons. Three more were
> added (`possible_duplicate`, `missing_from_ledger`, `unexplained_debit`), because without
> them the planted duplicate would be mislabelled `missing_from_bank`, and some rows would
> have no truthful label at all.

---

## 6. Determinism and replay

**Claim:** for the same three raw files and the same `ENGINE_VERSION`, `reconcile` returns
byte-identical output. The following measures make that hold:

| Risk | Measure |
|---|---|
| Wall-clock time in output | The engine package never reads the clock. `started_at` and `finished_at` live on the run row, outside `result_canonical`. Period boundaries come from the statement's own `FrToDt`. |
| Randomness or UUIDs | None in the engine. Output identifiers are ordinals (`match #12`) and natural keys (`ledger row 418`), never database IDs or UUIDs. |
| Database row order | The engine never reads parsed rows from the database; it parses the raw bytes itself. Database order, insert order and collation cannot affect it. |
| Dict and set iteration, `PYTHONHASHSEED` | Every collection the engine iterates is explicitly sorted by the keys in 5.3. String hashing randomisation therefore cannot change order, and a test proves it (below). |
| Floating point | There are no floats. The canonical serialiser **raises** if it meets a `float` anywhere in the result tree. |
| Serialisation | `json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True)` encoded as UTF-8, with only `str`, `int`, `bool`, `None`, `list` and `dict` allowed. Money in explanation text is formatted from integers by integer division. |
| Time zones | Dates are taken **as stated in the file** (`2026-09-30T23:30:00+02:00` → `2026-09-30`) with no conversion to UTC, which would move entries across day boundaries depending on the server. |
| Parser drift | The parser is part of the engine version. A run refuses to start if the files were parsed under a different `parser_version` than the running engine's. |
| Silent engine changes | A **golden test** stores the result SHA-256 for the seeded demo month per `ENGINE_VERSION`. Changing matching behaviour without bumping the version fails CI. |
| Forbidden imports | A static test walks the engine package's AST and fails on `time`, `random`, `uuid`, `secrets`, `os.environ`, `datetime.now`, `date.today` and `datetime.utcnow`. |

Canonical result shape (abridged):

```json
{"format":"recon-result/1","engine_version":"1.0.0",
 "config":{"EXACT_WINDOW_DAYS":5,"LEDGER_SETTLEMENT_WINDOW_DAYS":1,"PAYOUT_WINDOW_DAYS":3},
 "inputs":{"ledger":"<sha256 hex>","settlement":"<sha256 hex>","bank":"<sha256 hex>"},
 "statement":{"period_from":"2026-09-01","period_to":"2026-09-30"},
 "matches":[{"ordinal":1,"pass":"exact","ledger":[412],"settlement":[],"bank":[37],
             "explanation":"..."}],
 "exceptions":[{"ordinal":1,"reason":"missing_from_bank","ledger":[...],"settlement":[...],
                "bank":[],"fingerprint":"<hex>","explanation":"..."}],
 "summary":{"rows":{"ledger":0,"settlement":0,"bank":0},"matched":{...},"exceptions_by_reason":{...}}}
```

**Replay procedure:** `recon replay --run-id N` loads the run's three `import_file.raw`
values, checks that the engine version equals the run's, recomputes, stores a new run with
`replay_of_run_id = N`, and prints whether the hashes match. To replay a run made by an
older engine, check out the git tag `engine-vX.Y.Z` and run the same command.

**Tests that prove it** (written in the replay stage):
1. `test_reconcile_twice_byte_identical`: same raw bytes, two calls, equal bytes.
2. `test_replay_from_database_matches_original`: import the files into a fresh database in a
   different upload order, run, replay, and compare the stored hashes.
3. `test_hash_seed_independence`: run in subprocesses with `PYTHONHASHSEED=0` and
   `=4242`; outputs equal.
4. `test_golden_result_hash`: the demo month's hash equals the committed value for this
   engine version.
5. `test_engine_has_no_clock_or_randomness`: the AST check above.

---

## 7. File formats

All three generated files carry a visible **SYNTHETIC DEMO DATA** label inside the file.

### 7.1 Ledger CSV (the fintech's own export, invented by us; no external format applies)

```
# SYNTHETIC DEMO DATA - fictional company, fictional customers. Not real transactions.
entry_id,booked_on,entry_type,channel,payment_reference,currency,amount_minor,customer_ref,description
LE-000001,2026-09-01,payment,card,PAY-000001,EUR,4999,SYN-CUST-0042,Synthetic order
```
Leading `#` lines are skipped. The header must match exactly, and unknown columns reject
the file. `amount_minor` must match `^-?[0-9]+$`. Customer references are synthetic codes,
not names.

### 7.2 Orrery Payments settlement report CSV (fictional processor)

```
# SYNTHETIC DEMO DATA - Orrery Payments is a fictional processor. Not a real provider's output.
report_version,balance_transaction_id,created_on,line_type,merchant_reference,currency,gross_minor,fee_minor,net_minor,payout_id,payout_reference,payout_date
1,BT-0000001,2026-09-01,charge,PAY-000001,EUR,4999,95,4904,PO-20260903,ORR-PO-20260903,2026-09-03
```
- The column **structure** is modelled on a publicly documented itemized payout
  reconciliation report (source S5): per-transaction `gross`, `fee` and `net`, a reporting
  category, the payout ID, and a reference token that appears on the bank statement. It is
  not branded as, or presented as, any real provider's output.
- **Deliberate differences from that model:** amounts are integer minor units, not major
  units, and column names are our own.
- Orrery's fee in the synthetic data is **1.4% + EUR 0.25**, rounded half-up to the cent with
  integer arithmetic. It is a fictional price used only by the generator; the engine never
  assumes a fee schedule and trusts only the fee stated on each line.

### 7.3 Bank statement: ISO 20022 camt.053.001.02

Namespace `urn:iso:std:iso:20022:tech:xsd:camt.053.001.02`. Every imported bank file is
**validated against the official XSD (S1) before parsing**, and a file that fails is rejected
with the validator's message. The parser then reads only these elements, each confirmed
against the XSD's type definitions:

| XPath (under `Document/BkToCstmrStmt`) | XSD type | Use |
|---|---|---|
| `GrpHdr/MsgId`, `GrpHdr/CreDtTm` | `GroupHeader42` | provenance |
| `GrpHdr/AddtlInf` | `Max500Text` | carries the "SYNTHETIC DEMO DATA" label in generated files |
| `Stmt/Id`, `Stmt/FrToDt/FrDtTm`, `Stmt/FrToDt/ToDtTm` | `AccountStatement2`, `DateTimePeriodDetails` | statement period (**required** by us, optional in XSD) |
| `Stmt/Acct/Id/Othr/Id`, `Stmt/Acct/Ccy` | `CashAccount20`, `GenericAccountIdentification1` | account (synthetic ID; **no IBANs are generated**) |
| `Stmt/Bal[Tp/CdOrPrtry/Cd=OPBD|CLBD]/Amt[@Ccy]`, `CdtDbtInd`, `Dt/Dt` | `CashBalance3`, `BalanceType12Code` | balance tie-out |
| `Stmt/Ntry/Amt[@Ccy]`, `CdtDbtInd`, `Sts`, `RvslInd` | `ReportEntry2` | amount and direction; `Sts` must be `BOOK`; `RvslInd=true` is rejected in Level 1 |
| `Stmt/Ntry/BookgDt/(Dt|DtTm)`, `ValDt/(Dt|DtTm)` | `DateAndDateTimeChoice` | dates |
| `Stmt/Ntry/NtryRef`, `AcctSvcrRef` | `Max35Text` | provenance |
| `Stmt/Ntry/BkTxCd/Domn/Cd`, `Fmly/Cd`, `Fmly/SubFmlyCd` | `BankTransactionCodeStructure4/5/6` | generator emits `PMNT/RCDT/ESCT` (received SEPA credit transfer, per S4) |
| `Stmt/Ntry/NtryDtls/TxDtls/Refs/EndToEndId` | `TransactionReferences2` | processor payout reference |
| `Stmt/Ntry/NtryDtls/TxDtls/RmtInf/Ustrd` | `RemittanceInformation5` (`Max140Text`, 0..n) | direct-transfer reference |
| `Stmt/Ntry/NtryDtls/TxDtls/RltdPties/Dbtr/Nm` | `TransactionParty2` | display only (synthetic names) |

Level 1 rejects these cases rather than guessing: more than one `Stmt` per file, more
than one `TxDtls` per `Ntry` (a batch-booked entry), any `Sts` other than `BOOK`,
`RvslInd = true`, non-EUR amounts, and a failed balance tie-out.

`NtryDtls/Btch` is **not** used for processor payouts. S4 describes it as carrying the
account owner's *own* payment batch identifiers returned to them (pain.001 batches), which is
not what an incoming processor payout is. A payout arrives as one ordinary credit transfer
whose `EndToEndId` carries the processor's payout reference.

---

## 8. Synthetic data generator and planted problems

`recon generate --seed 20260901 --month 2026-09 --out demo-data/` writes the three files plus
`planted.json`, a machine-readable list of every planted problem and the exception or match
expected for it. The same seed gives byte-identical files, and a test proves it. The PRNG
is `random.Random(seed)`; the generator is not part of the engine, so seeded randomness is
allowed there.

Realistic background for the month: about 600 card payments (EUR 5 to EUR 400) paid out daily
on T+2 with weekend roll-forward, a handful of refunds netted inside payouts, a few
low-volume days with a single payment (so pass B has real work), and about 40 direct bank
transfers. Customer and company names are synthetic codes ("Synthetic Customer 0042").

| # | Planted problem | How it is planted | Expected outcome |
|---|---|---|---|
| P1 | **Missing payout** | One payout group (about 9 payments) is in the settlement report with a September `payout_date`, but no bank entry exists | 1 exception `missing_from_bank` covering the group's settlement and ledger rows |
| P2 | **Fee mismatch** | A single-payment payout where the bank received EUR 0.40 less than the report's `net` | 1 exception `amount_mismatch`: "gross − deposit = EUR 2.05; stated fee EUR 1.65; unexplained EUR 0.40" |
| P3 | **50-to-1 batch deposit** | One day with exactly 50 card payments → one payout → one bank credit | 1 match, pass `many_to_one`, citing 50 ledger + 50 settlement + 1 bank row |
| P4 | **Duplicate ledger entry** | One card payment written twice in the ledger (different `entry_id`, same reference, amount and date) | The original is matched; the copy becomes 1 exception `possible_duplicate` citing the matched entry |
| P5 | **Unexpected deposit** | One bank credit (`PMNT/RCDT/ESCT`) with a remittance text and no counterpart anywhere | 1 exception `unknown_deposit` |
| (natural) | Timing | Card payments on 29–30 Sep whose payouts are dated 1–2 Oct; one direct transfer booked 30 Sep that the bank books in October | `timing` exceptions; listed in the README as expected, not as problems |

The acceptance test `test_all_planted_problems_caught` reads `planted.json`, runs the
engine, and asserts each planted item produced exactly its expected outcome.

---

## 9. Web UI (built last)

Pages: **Upload** (three file slots; shows "already imported" on hash collision) → **Runs**
(engine version, input hashes, started/finished, result hash, replay status) → **Run detail**
(summary by pass and reason, matches with their cited rows) → **Exceptions queue**
(filter by reason or open/resolved) → **Exception detail** (rows involved, suggested reason,
explanation, full resolution history, resolve or correct form). A persistent banner on every
page reads **"Synthetic demo data. Fictional processor and bank."**

**Staff identity (decided 2026-10-02, D-031):** resolutions need a `resolved_by`. The UI has
a **"sign in as" picker** over seeded synthetic staff users (`staff_user.is_synthetic =
true`), with a signed session cookie and no passwords, labelled demo-only on the picker and
in the banner. Every resolution records the signed-in user. Rejected: a shared demo password
(adds nothing for a public demo) and real authentication (needs a third-party provider and
is beyond what Level 1 demonstrates).

---

## 10. Sources

Retrieved 2026-10-02. SHA-256 hashes are of the files exactly as downloaded.

| ID | Source | Version | URL | SHA-256 | Used for |
|---|---|---|---|---|---|
| **S1** | **ISO 20022 official XML schema, camt.053.001.02** (BankToCustomerStatementV02), generated by SWIFTStandards Workstation 2009-01-08 | camt.053.001.02 | Original: `http://www.iso20022.org/documents/messages/1_0_version/camt/schemas/camt.053.001.02.zip`. Retrieved via Internet Archive snapshot 2014-03-29: `https://web.archive.org/web/20140329024633id_/http://www.iso20022.org/documents/messages/1_0_version/camt/schemas/camt.053.001.02.zip` | zip `3517789f…0467`; xsd `d664afd1…f2f3d` | XSD validation; every element name in 7.3. Committed unmodified at `docs/sources/iso20022/camt.053.001.02.xsd` |
| **S2** | **ISO 20022 official sample instance, camt.053.001.02** | camt.053.001.02 (2009-04-17) | Original: `http://www.iso20022.org/documents/messages/camt/instances/camt.053.001.02.zip`. Via Internet Archive snapshot 2014-03-29: `https://web.archive.org/web/20140329034110id_/http://www.iso20022.org/documents/messages/camt/instances/camt.053.001.02.zip` | zip `bdb2b598…7a79`; xml `d50eddff…bbc20` | Parser test fixture. It **validates against S1** (checked with lxml 5.3.0). Committed unmodified |
| S3 | Dutch Payments Association, *XML message for Bank to Customer Statement (camt.053), Implementation Guidelines for the Netherlands* | v1.1, October 2013 (covers camt.053.001.02) | https://www.betaalvereniging.nl/wp-content/uploads/2026/03/IG-Bank-to-Customer-Statement-CAMT-053-v1-1.pdf | `0d121e71…53dc` | Corroborates element usage; Annex H reproduces S2 |
| S4 | Finance Finland, *ISO 20022 Account Statement Guide* | V1.4, 15 January 2020 (covers camt.053.001.02) | https://www.finanssiala.fi/wp-content/uploads/2021/03/ISO-20022-Account-Statement-Guide-2020.pdf | `c3f2f282…08c6` | Bank transaction code `PMNT/RCDT/ESCT` for a received SEPA credit transfer; meaning of `NtryDtls/Btch` |
| S5 | Stripe documentation, *Payout reconciliation report*, itemized report type `payout_reconciliation.itemized.7` | page as of 2026-10-02 | https://docs.stripe.com/reports/payout-reconciliation | (web page) | **Structure only** for the fictional Orrery report: gross/fee/net per transaction, payout ID, payout reference token shown on the bank statement |
| S6 | ISO 4217 currency code list ("List One"), SIX Group (ISO 4217 maintenance agency) | `Pblshd="2026-09-17"` | https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml | `33139b43…b0ff` | EUR minor units = 2 (`CcyMnrUnts`) |

**What could not be obtained directly, and what was done instead:** `www.iso20022.org`
returned HTTP 403 to every automated request (plain HTTP client and fetch tool) on
2026-10-02. S1 and S2 were therefore retrieved from the Internet Archive's copies of
iso20022.org's **own download URLs**, and S2 was confirmed to validate against S1. I have not
written any XML structure from memory.

**Independent confirmation (2026-10-02):** the project owner downloaded camt.053.001.02 from
the ISO 20022 message archive in a browser (archive package
`archive_banktocustomer_cash_management_2_8b2dd68643`) and computed its SHA-256 with
`certutil -hashfile camt.053.001.02.xsd SHA256`. The result,
`d664afd198d1f36386a14e2bfd0505c80c1291a5357d8132aa6ab5443a6f2f3d`, is **identical** to the
committed S1. The archived copy and the file ISO currently publishes are the same bytes.

Not used, because they are not needed in Level 1: exchange rates, holiday calendars, IBAN
check-digit rules (no IBANs are generated).

---

## 11. Build order and test plan

| Stage | Deliverable | Tests written alongside |
|---|---|---|
| 1. Schema | migrations 0001–0006 and runner | constraint tests against a real Postgres (Docker locally, Actions service container in CI): duplicate hash rejected; raw immutable; allocation PK and XOR; cross-file allocation rejected; finish check rejects bad arithmetic and incomplete allocation; resolution append-only, note length, single chain |
| 2. Generator | `recon generate`, `planted.json`, `demo-data/` | same seed gives identical bytes; bank file validates against S1; balances tie out; every file carries the synthetic label |
| 3. Import | parsers, `recon import`, parse jobs | S2 parses with expected values; idempotent re-import; every rejection rule listed in 7.3 |
| 4. Matching | engine passes A to C and classification | per-pass unit tests on hand-built cases; ambiguity and leftover tests; planted-problem acceptance test |
| 5. Exceptions | resolution writes (worker CLI and SQL), reason codes | append and correct chain; update and delete blocked; unfinished run rejected |
| 6. Replay | `recon replay`, golden hash | the five tests in section 6 |
| 7. Web UI | Next.js pages, upload, resolve | API route tests for idempotent upload and resolution validation; amount formatting has no `Number()` path |

Each stage ends with the five-part report the brief asks for.

---

## 12. Not in Level 1 (later work, not built)

Multi-currency and FX (needs a cited rate source); chargebacks arriving after period close;
one-to-many splits (one ledger payment settled across several deposits); period close and
post-close adjustments; ageing dashboards; multi-entity. Also out: camt.053 versions other
than .02, multi-account statements, batch-booked bank entries, reversals, pending entries,
and carrying resolutions across runs (the fingerprint is stored to enable it later).

## 13. Noticed, not done

- **Tamper evidence for resolutions:** triggers and grants stop the application from editing
  history, but not a database owner. A hash chain (each resolution stores
  `sha256(prev_hash ‖ row)`), or periodic export of the chain hash, would make tampering
  detectable.
- **Fee schedule verification:** the engine trusts the fee stated on each line. Checking it
  against the contracted price list would catch a processor overcharging consistently, which
  this design cannot see.
- **Bank holiday calendar** (e.g. TARGET2 closing days), to tighten the payout window. This
  needs a cited source.
- **Real authentication for staff:** the demo's "sign in as" picker proves who resolved what
  inside the demo, but anyone can pick any user. A production version needs real identity
  (SSO) and role separation, for example so the person who resolves an exception cannot also
  approve it.
- **Newer camt.053 versions:** the parser
  dispatches on namespace, so adding one means a new XSD source and a new parser module.
