# Decision Log

One entry per design decision: what was decided, the alternative rejected, and why.
Status values: **accepted** (follows from the brief, the sources, or owner approval), **proposed**
(awaiting approval with `design.md`), **superseded** (kept for history, never deleted).

---

### D-001: Money as `bigint` minor units with an explicit currency code
**Status:** accepted (brief)
**Decision:** Every amount is an integer count of minor units next to an ISO 4217 code.
**Rejected:** `float`/`double` (inexact: 0.1 + 0.2 ≠ 0.3, so phantom differences appear in
exactly the comparisons this system exists to make). Also rejected: `numeric(p,s)`. It is
exact, but the scale lives in the column definition, not with the currency, and JavaScript
has no exact decimal type, so values would be converted at the web boundary anyway. Integers
behave the same in SQL, Python and JS (`BigInt`).

### D-002: camt.053 version **.001.02**
**Status:** accepted (approved by project owner, 2026-10-02)
**Decision:** Level 1 accepts only `urn:iso:std:iso:20022:tech:xsd:camt.053.001.02`.
**Rejected:** Any later camt.053 version. For .02 I hold both the official XSD and an official
sample instance (S1, S2, with S1 independently hash-confirmed), and both published national
implementation guides I obtained (Dutch Payments Association, S3; Finance Finland, S4)
describe .02. For no later version do I hold the same set of verified material. The parser
dispatches on the XML namespace, so supporting a newer version is additive (a new XSD source
and a new parser module) rather than a rewrite.
**Not claimed:** which camt.053 version banks issue today. That has not been verified, and the
choice above does not depend on it.

### D-003: Validate every bank file against the official XSD before parsing
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** `lxml` validates against the committed, unmodified S1, and a failure rejects the
file.
**Rejected:** Lenient parsing that reads the elements we need and ignores the rest. That would
let a malformed or wrong-version file produce plausible-looking rows. Fail closed.

### D-004: No IBANs anywhere in synthetic data
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** The account is identified by `Acct/Id/Othr/Id` (e.g. `SYNTHETIC-DEMO-ACCT-0001`),
which the XSD permits. The official sample S2 does the same.
**Rejected:** Generated IBANs with valid check digits. A syntactically valid IBAN can coincide
with a real account, and the brief forbids real IBANs. It also avoids needing a cited source
for IBAN country formats.

### D-005: SHA-256 as a database-generated column with a unique constraint
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** `sha256 bytea GENERATED ALWAYS AS (sha256(raw)) STORED UNIQUE`.
**Rejected:** Computing the hash in the web or worker and inserting it. A bug or alternative
code path could then store a hash that does not match the bytes. Here the database derives it.

### D-006: Raw files stored in Postgres (`bytea`), immutable
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** Keep uploads in `import_file.raw`; triggers block `UPDATE` and `DELETE`.
**Rejected:** Object storage (Vercel Blob, S3). It is another service, the file and its parsed
rows could not be written in one transaction, and the free-tier limits are irrelevant at a few
hundred KB per month.

### D-007: The engine is a pure function of the raw bytes
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** `reconcile(ledger_bytes, settlement_bytes, bank_bytes)` parses and matches
in memory. The database tables are a projection of its result.
**Rejected:** An engine that queries parsed rows from Postgres. Its output would then depend on
query order, collation and the parsed tables never having changed, and replay would only be as
good as those assumptions.

### D-008: Determinism is proven on canonical bytes, not on database rows
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** The result is a canonical JSON byte string (sorted keys, no whitespace, no
floats), and its SHA-256 is stored on the run.
**Rejected:** Comparing match rows between two runs in SQL. Surrogate IDs differ by design, and
"equal rows" needs an equality definition that is itself open to dispute. A hash comparison is
one line and cannot be argued with.

### D-009: Wall-clock times live on the run record, never in the result
**Status:** accepted (brief)
**Decision:** `started_at` and `finished_at` are columns. The engine never reads the clock, and
a static test enforces it.
**Rejected:** Putting a "computed at" timestamp in the result, which would make every replay
differ.

### D-010: Allocation tables per input kind, with primary key `(run_id, row_id)`
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** `allocation_ledger`, `allocation_settlement` and `allocation_bank`, each row
pointing at exactly one match **or** one exception.
**Rejected:** (a) A polymorphic `match_item(source_kind, row_id)`, which cannot carry a foreign
key to the row it cites. (b) A `UNIQUE` on the row ID alone, which would forbid replaying the
same files in a new run. (c) Separate match-item and exception-item tables, which cannot
stop a row being both matched and in an exception.

### D-011: Composite foreign keys tie allocations to the run's own files
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** The allocation's `*_file_id` column takes part in two FKs, one to the run's file
slot and one to the row's file, and generated constant `*_kind` columns on the run pin each
slot to the right file kind.
**Rejected:** A trigger or application check. A declarative FK cannot be bypassed by a
forgotten code path and is visible in the schema itself.

### D-012: The database independently verifies each match's arithmetic at run finish
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** A trigger on `running → finished` checks every row is allocated exactly once and
re-verifies the sums each pass claims.
**Rejected:** Trusting the engine alone. The brief asks for constraints "in the database, not
only in code", and a second implementation of the invariants in SQL catches engine bugs that
tests did not anticipate.

### D-013: Many-to-one matching groups by the processor's payout reference
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** A batch is the set of settlement lines sharing `payout_reference`, verified
against the bank deposit and ledger.
**Rejected:** A subset-sum search over unmatched payments for combinations equal to the deposit.
It is computationally hard, and when two subsets fit, picking one is a guess presented as a
match. That is the opposite of surviving questioning.

### D-014: Batch matching is all-or-nothing, with zero tolerance
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** If any line in a batch lacks a ledger counterpart, or any sum is off by one cent,
the batch is an exception.
**Rejected:** (a) Partial batch matches. (b) An amount tolerance (e.g. ±EUR 0.05). Both make
small real differences, such as the planted EUR 0.40 fee mismatch, disappear.

### D-015: Stable tie-breaking, with every leftover surfaced
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** When several candidates fit, take the first in a documented sort order. The
others remain unallocated and become exceptions.
**Rejected:** Refusing to match whenever more than one candidate fits. With a duplicated ledger
entry that would push the genuine payment into the exceptions queue too, giving two exceptions
for one problem, and the duplicate would no longer be identifiable as the extra copy.

### D-016: Calendar-day windows; no holiday calendar
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** Date windows are in calendar days (5 / ±1 / 3), and the payout window absorbs
weekends.
**Rejected:** Business-day windows with a TARGET2 holiday calendar. That is reference data
needing a cited source (the brief's rule), and the tighter window is not needed to catch any
Level 1 problem. Listed as later work.

### D-017: One exception per problem, not one per row
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** Unmatched rows are grouped (by payout group, then duplicate, then timing) so a
missing payout of nine payments is one exception citing eighteen rows.
**Rejected:** One exception per unmatched row. Nine identical "missing from bank" items for one
missing payout is noise a Head of Finance would have to regroup by hand.

### D-018: Three suggested reasons beyond the brief's four
**Status:** accepted (approved by project owner, 2026-10-02)
**Decision:** Add `possible_duplicate`, `missing_from_ledger` and `unexplained_debit`.
**Rejected:** Forcing every leftover into the four listed reasons. The duplicate ledger entry
would be labelled `missing_from_bank` (untrue: the payment did arrive, once), and a bank debit
cannot truthfully be called an "unknown deposit".

### D-019: Resolutions are an append-only linear chain per exception
**Status:** accepted (brief; mechanism approved with design, 2026-10-02)
**Decision:** Corrections insert a new row with `supersedes_id`. A partial unique index allows
one root per exception, and `UNIQUE (supersedes_id)` allows each resolution to be superseded at
most once. Triggers block `UPDATE`, `DELETE` and `TRUNCATE`, and the web role lacks the
privileges.
**Rejected:** (a) A mutable `status` column on `exception`. (b) Soft-delete flags. Both rewrite
history. A tree of corrections (several rows superseding one) is also rejected, because it makes
"which resolution is in force" ambiguous.

### D-020: Mandatory note is enforced in the database (at least 10 non-blank characters)
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** `CHECK (char_length(btrim(note)) >= 10)`.
**Rejected:** `NOT NULL` alone, which accepts `''` and `' '`. A form-only check would be bypassed
by any other writer. The threshold of 10 is a judgement call; open to change.

### D-021: Resolutions attach to one run's exceptions; a fingerprint is stored for later
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** `resolution.exception_id` refers to a specific run's exception. Each exception
also stores `sha256(reason ‖ sorted row keys)`.
**Rejected:** Cross-run carry-over now. Deciding when a re-run's exception is "the same" as an
earlier one is a policy question (what if the engine version changed?) that belongs with
period close, which is out of scope.

### D-022: Worker runs via GitHub Actions (cron + `workflow_dispatch`) from a job table
**Status:** accepted (approved with design, 2026-10-02; matches the practice's Demo 1 stack per the brief)
**Decision:** The web inserts `job` rows, and the worker claims them with
`FOR UPDATE SKIP LOCKED`.
**Rejected:** A Python serverless function on Vercel. It is feasible, but it splits the worker's
runtime across two platforms and has execution-time limits; the brief fixes GitHub Actions for
scheduled jobs.

### D-023: Fictional settlement report modelled on a documented itemized payout report
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** Orrery's CSV follows the *structure* of S5 (per-line gross/fee/net, payout ID,
payout reference that appears on the bank statement), with our own column names and integer
minor units.
**Rejected:** (a) Copying a real provider's column names verbatim, which risks presenting the
file as that provider's output. (b) An invented structure with no grounding, which a CTO
reviewer would rightly question. An Adyen settlement-detail page was also checked as a
second model but returned 404 on 2026-10-02, so it is not cited.

### D-024: Bank-side references: `EndToEndId` for payouts, `RmtInf/Ustrd` for direct transfers
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** Exact equality on those two fields only.
**Rejected:** Substring or fuzzy matching over free text. A "fuzzy" match is a guess, and
guesses are what a later dispute attacks. `NtryDtls/Btch` was also rejected for payouts: S4
describes it as the account owner's own outgoing batch identifiers.

### D-025: Level 1 rejects unsupported camt.053 features instead of approximating them
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** Reject files with multiple `Stmt`, multi-`TxDtls` entries, `Sts` ≠ `BOOK`,
`RvslInd = true`, non-EUR amounts, or a failed balance tie-out.
**Rejected:** Importing them and "doing our best". Each is a real-world feature with semantics
(reversals, batch bookings, pending items) that Level 1 does not model; silently
mis-modelling them is worse than refusing.

### D-026: Dates are taken as stated in the file, with no time-zone conversion
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** `2026-09-30T23:30:00+02:00` is the business date `2026-09-30`.
**Rejected:** Normalising to UTC. That shifts late-evening entries into the next day, possibly
the next month, depending on the stated offset, creating timing exceptions that do not exist.

### D-027: Constraint tests run against real PostgreSQL, never mocks
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** Docker Postgres locally and a service container in GitHub Actions, both free.
**Rejected:** A mocked database or SQLite. The constraints *are* the product's guarantees;
testing them anywhere but Postgres tests nothing.

### D-028: Engine purity is enforced by a static test
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** An AST walk over the engine package fails on clock, randomness, UUID and
environment access.
**Rejected:** Relying on code review. A determinism guarantee that rests on reviewers noticing
a `datetime.now()` is not a guarantee.

### D-029: Golden result hash per engine version
**Status:** accepted (approved with design, 2026-10-02)
**Decision:** The demo month's result SHA-256 is committed per `ENGINE_VERSION`, and a
behaviour change without a version bump fails CI.
**Rejected:** Version bumps by convention only. Then "same engine version" would not actually
mean "same behaviour", which is the premise of replay.

### D-030: Official ISO files obtained via Internet Archive copies of iso20022.org URLs
**Status:** accepted; independently confirmed 2026-10-02 (see end of entry)
**Decision:** iso20022.org returned 403 to automated retrieval. S1/S2 were downloaded from
web.archive.org snapshots of iso20022.org's own download paths, and S2 was confirmed to
validate against S1.
**Rejected:** (a) Third-party re-hosted XSDs (GitHub mirrors, vendor sites), whose provenance is
weaker than an archived copy of the official URL. (b) Writing the structure from memory, which
the brief forbids.
**Confirmation:** The project owner downloaded camt.053.001.02 from the ISO 20022 message
archive in a browser and ran `certutil -hashfile camt.053.001.02.xsd SHA256`. The result is
`d664afd198d1f36386a14e2bfd0505c80c1291a5357d8132aa6ab5443a6f2f3d`, identical to the
committed file, so the archive route produced the official bytes.

### D-031: Staff identity for resolutions
**Status:** accepted (chosen by project owner, 2026-10-02; see `design.md` §9)
**Decision:** A "sign in as" picker over seeded synthetic staff users with a signed session
cookie and no passwords, labelled demo-only. `resolution.resolved_by` records the signed-in
user.
**Rejected:** (a) A shared demo password plus picker, which adds friction without adding
assurance on a public demo. (b) Real authentication, which needs an external identity or email
provider and is not what Level 1 demonstrates. Recorded under "Noticed, not done" for a
production version.

---

## Stage 1 (schema), 2026-10-02

### D-032: Parsed rows and runs reference `import_parse (file, kind, 'parsed')`
**Status:** accepted (refinement of design §4.1–4.3, made while building)
**Decision:** Every row table and every run slot carries generated constant columns (`file_kind`,
`parse_status = 'parsed'`) and one composite foreign key to
`import_parse (import_file_id, kind, status)`. A single declarative constraint proves the row
or slot points at a file of the right kind **whose parse succeeded**.
**Rejected:** Pointing rows at `import_file (id, kind)` as the design first sketched. That proves
the kind but not the parse outcome: rows could be attached to a file later recorded as
rejected, or a run could use a file that was never parsed. Because `import_parse` is
append-only, a rejected file can never acquire rows.

### D-033: Tables named `run_match` and `run_exception`, not `match` and `exception`
**Status:** accepted (rename of design §4.4)
**Decision:** Prefix both with `run_`.
**Rejected:** The design's `match` and `exception`. `MATCH` is an SQL keyword and `EXCEPTION`
is a PL/pgSQL block keyword, and the finish-check trigger queries both tables from inside
PL/pgSQL. Relying on the parser to disambiguate is a latent bug. The prefix also states the
real scope: both are per-run.

### D-034: Project-specific SQLSTATEs for trigger errors
**Status:** accepted
**Decision:** `RC001` append-only violation, `RC002` illegal run state change, `RC003` finish
check failed, `RC004` resolution on an unfinished run.
**Rejected:** The generic `P0001` (`raise_exception`). With one shared code, a test asserting
"the write failed" could pass because of an unrelated error. Distinct codes let every test
assert the *specific* guarantee that fired, and let the worker report errors precisely.

### D-035: All pending migrations apply in one transaction under an advisory lock
**Status:** accepted
**Decision:** The runner takes `pg_advisory_xact_lock`, verifies the recorded hash of every
applied file, and applies all pending files plus their ledger rows in a single transaction.
**Rejected:** One transaction per migration. A failure halfway through would leave the database
at an intermediate version that no one tested. PostgreSQL DDL is transactional, so
all-or-nothing costs nothing.

### D-036: Database tests fail, never skip, when no test database is configured
**Status:** accepted
**Decision:** Without `RECON_TEST_ADMIN_URL`, every database test fails with an instruction.
**Rejected:** `pytest.skip`. A summary of "120 passed, 158 skipped" reads as green at a glance,
and for a project whose guarantees *are* database constraints, a skipped constraint test is
the most misleading outcome possible.

### D-037: Proving the tests can fail, by weakening the schema on purpose
**Status:** accepted (practice for each stage)
**Decision:** After the suite passed, a copy of the migrations was weakened three ways and the
suite run against it: the resolution append-only trigger removed, the finish check's
"payout is a credit" and "one payout per batch" conditions removed, and the settlement
`net = gross − fee` CHECK removed. Exactly the targeted tests failed (5) and no others. The
real migrations were not modified.
**Rejected:** Trusting a green first run. A constraint test that cannot fail is decoration.

### D-038: `current_resolution` view
**Status:** accepted (small addition to design §4.5)
**Decision:** A view returns, per exception, the resolution not superseded by any other: the
tail of the chain.
**Rejected:** Letting every reader (web, worker, reports) re-implement "which resolution is in
force". One definition in the database cannot drift between consumers.
