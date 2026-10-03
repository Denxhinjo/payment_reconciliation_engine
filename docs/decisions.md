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

## Stage 1 follow-up: Neon verification, 2026-10-02

Migrations were run once against a throwaway Neon branch (`roles-test`, PostgreSQL 18.6, since
deleted). All six applied, **including role creation in 0006**. The full suite then gave
157 passed / 1 failed, and the failure exposed a test weakness recorded below.

### D-039: Role privileges are tested from the catalog; SET ROLE tests are a bonus
**Status:** accepted (chosen by project owner, 2026-10-02)
**Finding:** On Neon the deploying owner (`neondb_owner`, CREATEROLE, not superuser) holds
ADMIN over the roles it creates but neither INHERIT nor SET (PostgreSQL 16+ behaviour). The
old role tests switched roles *inside* the block that expected error 42501, so on Neon the
42501 came from `SET ROLE` itself, and 12 "lacks privilege" tests **passed without testing
anything**. Locally they were valid only because the test user is a superuser.
**Decision:** (1) An exhaustive catalog test: for every table and view and every privilege
type, `has_table_privilege()` for each role must equal an explicit expected matrix, so missing
*and extra* grants fail. This needs no role membership and behaves identically on Docker and
Neon. (2) Two end-to-end tests remain, with `SET ROLE` moved into a fixture *outside* the
asserted block, so a 42501 can only come from the statement under test. They skip only when
the server forbids the test connection to SET ROLE.
**Rejected:** (a) A migration granting the owner SET on both roles: it works, but changes the
schema to suit the tests. (b) Documenting the limitation only, which would leave Neon without
a role test.
**Exception to D-036 (fail, never skip):** the end-to-end skip is allowed because the same
guarantee is covered by the catalog test on every server. Nothing untested hides behind it.
**Verified:** an extra `GRANT UPDATE ON resolution TO recon_web`, added to a copy of the
migrations, fails both the matrix test and the end-to-end test.

### D-040: Tests run on PostgreSQL 18, the deployment target's major version
**Status:** accepted (chosen by project owner, 2026-10-02)
**Decision:** The local Docker test server and the CI service container use `postgres:18-alpine`,
matching the Neon branch (18.6).
**Rejected:** (a) Staying on 17, which tests a different major version from production. (b) A
17 + 18 CI matrix: there is no deployment on 17 to protect.

## Stage 2 (synthetic generator), 2026-10-02

### D-041: Planted problems are placed by fixed rules, not at random
**Status:** accepted
**Decision:** The seeded RNG produces the background (amounts, customers, daily volumes,
refund picks). Each planted problem sits on a day chosen by an explainable rule: P1 on the
second Monday, P3 on the third Wednesday, P2 on the second single-payment Sunday, and so on.
`planted.json` records the exact rows.
**Rejected:** Random placement. A random P1 could land on a payout dated after the period end
and become indistinguishable from a timing difference, so the demo's claim "catches all
five" would depend on the seed.

### D-042: The demo month is committed, and a test proves it reproducible
**Status:** accepted
**Decision:** `demo-data/2026-09/` is committed. A test regenerates it from seed 20260901 and
compares bytes, and `.gitattributes` marks the directory `-text` so git never rewrites line
endings.
**Rejected:** (a) Generating at build time only: reviewers could not read the files without
running code. (b) Committing without the reproduction test: the files and the generator could
silently drift apart.

### D-043: An independent discrepancy derivation is the planted-problem test
**Status:** accepted
**Decision:** The central generator test re-derives every disagreement between the three files
without reading `planted.json`, and requires that set to equal the planted and timing items
exactly.
**Rejected:** Checking only that the planted rows exist. That cannot detect an accidental,
unplanned discrepancy, which would later show up as a "caught problem" nobody planted.
**Verified:** with the P2 shortfall set to zero in memory, this test fails, along with the
reproducibility test.

### D-044: The generator refuses to write a bank file that fails the official XSD
**Status:** accepted
**Decision:** `_bank_xml` validates its own output against S1 before returning it.
**Rejected:** Validating only in tests: a future change could write an invalid file to
`demo-data/` between test runs.

### D-045: Direct transfers carry no `EndToEndId`; the reference is in `RmtInf/Ustrd`
**Status:** accepted
**Decision:** For customer bank transfers the generator omits `Refs` entirely. The payment
reference is the unstructured remittance text, matched by exact equality (D-024).
**Rejected:** A placeholder `EndToEndId` value for "not provided". The specific conventional
value comes from SEPA rulebooks this project has not obtained as a source, so it is not used.

### D-046: Statement timestamps are UTC (`Z`)
**Status:** accepted
**Decision:** `CreDtTm`, `FrDtTm` and `ToDtTm` are written in UTC.
**Rejected:** Local time with an offset (e.g. `+02:00`). Picking the right offset for each
month needs daylight-saving rules, which are reference data; UTC needs none. Business dates
are still read as stated (D-026).

### D-047: Bank files declaring a DTD are refused, and validator crashes become rejections
**Status:** accepted (found by the hostile-input test)
**Decision:** `camt053.validate` refuses any document containing `<!DOCTYPE`, and converts an
lxml `XMLSchemaValidateError` into an ordinary `Camt053Error`.
**Finding:** With entity expansion disabled, an entity reference stays unexpanded in the tree,
and lxml's schema validator then raised an *internal error* instead of a validation failure,
which would have crashed the importer instead of rejecting the file.
**Rejected:** Relying on `resolve_entities=False` alone. It prevents the data leak but not the
crash, and a camt.053 statement has no legitimate use for a DTD.

### Open item for stage 4: timing rule for payouts dated on the last days of the period
**Status:** superseded by D-048 (owner decision, 2026-10-03); kept for history
**Observation:** Design §5.7 classifies an unmatched payout group as `timing` only if
`payout_date > period_to`. A payout dated on the last day of the month (or within
`PAYOUT_WINDOW_DAYS` of it) that the bank books *after* the period would be labelled
`missing_from_bank`, a false alarm. The ledger-side rule already uses
`booked_on + window > period_to`. The generator avoids the case: bank bookings never cross the
period end. So the demo month is unaffected either way, but real data would hit it.
**Proposed:** use `payout_date + PAYOUT_WINDOW_DAYS > period_to` for payouts too, matching the
ledger rule. Trade-off: a payout that genuinely went missing in the last 3 days of a month is
reported as `timing` first. It would surface as missing in the next period's run, which is
period-close work outside Level 1.

## Before stage 3, 2026-10-03

### D-048: Timing rule uses the matching window, and every timing exception states its deadline
**Status:** accepted (owner decision, 2026-10-03). **Supersedes** the payout timing condition
in design §5.7 step 1 and the open item "timing rule for payouts dated on the last days of the
period" above. Both are kept unedited for history.
**Old rule:** an unmatched payout group with no bank entry is `timing` only if
`payout_date > period_to`; otherwise `missing_from_bank`.
**New rule:**
- *Payout group with no bank entry:* `timing` if `payout_date + PAYOUT_WINDOW_DAYS > period_to`,
  otherwise `missing_from_bank`. Deadline = `payout_date + PAYOUT_WINDOW_DAYS`.
- *Direct-transfer ledger row with no bank entry:* `timing` if
  `booked_on + EXACT_WINDOW_DAYS > period_to`. Deadline = `booked_on + EXACT_WINDOW_DAYS`.
- *Card ledger row with no settlement line:* `timing` if
  `booked_on + LEDGER_SETTLEMENT_WINDOW_DAYS > period_to`. Deadline = `booked_on +
  LEDGER_SETTLEMENT_WINDOW_DAYS`. This defines the "window" that design §5.7 step 3 left
  generic.
- *Every* `timing` explanation states the source date, the window and the deadline, and what
  to do after it, e.g. *"Payout PO-20260930 dated 30 Sep 2026; payout window 3 days; expected
  at the bank by 3 Oct 2026; if not booked by then, treat as missing."*
**Why:** under the old rule, a payout dated on the period's last day and booked by the bank one
day later (inside the window) was labelled `missing_from_bank`, a false alarm on routine
timing. The ledger-side rule already used the window, and the two now agree.
**Trade-off, accepted:** a payout that genuinely goes missing within `PAYOUT_WINDOW_DAYS` of the
period end is reported as `timing`, not `missing_from_bank`, in this period's run. The
explicit deadline in the explanation is the mitigation: the reviewer is told the date after
which "timing" is no longer a credible explanation. Following it up in the next period is
period-close work, outside Level 1.
**Rejected:** (a) Keeping the old rule: false alarms on routine month-end timing train
reviewers to ignore `missing_from_bank`. (b) Waiting for the next period's statement before
classifying: that needs cross-period state, which Level 1 does not have.
**Tests committed for stage 4 (hand-built, not by regenerating the demo month):**
(a) a payout dated on the statement's last day that the bank books after the period → `timing`,
with its deadline in the explanation; (b) a payout dated within the window that never arrives
→ also `timing`, with the explanation stating the deadline.

### D-049: Incident: 12 role tests passed without testing anything
**Status:** accepted (record of an incident found 2026-10-02; fix is D-039)
**What passed vacuously:** the 12 stage-1 tests asserting that `recon_web` (7 statements) and
`recon_worker` (5 statements) are refused by *privilege* (SQLSTATE 42501) when they try to
change evidence.
**Why:** each test ran `SET LOCAL ROLE <role>` *inside* the block that expected 42501. Locally
and in CI the test connection is a superuser, so `SET ROLE` succeeded and the statement
under test produced the 42501: the tests were valid there. On Neon the connection is
`neondb_owner`, a non-superuser with CREATEROLE. Since PostgreSQL 16 a role's creator gets
ADMIN on it but not the SET option, so `SET ROLE` itself failed with 42501, before the
statement ran. The expected error arrived for the wrong reason and all 12 tests passed.
**How it was caught:** not by those 12 tests. The one *positive* role test
(`test_web_role_can_insert_a_resolution`) had no expected error to hide behind and failed
with "permission denied to set role". Reading that failure showed that the negative tests
shared the same `SET ROLE` and therefore could not have been testing anything. The catalog
then confirmed the cause: the owner's membership in `recon_web` is admin=true, inherit=false,
set=false.
**What replaced them (D-039):** an exhaustive catalog test that compares each role's privileges
on every table and view with an explicit expected matrix, needing no `SET ROLE`; plus two
end-to-end tests whose `SET ROLE` happens in a fixture *outside* the asserted block, so a 42501
can only come from the statement itself. The replacement was proven able to fail: an extra
`GRANT UPDATE ON resolution TO recon_web` fails both.
**Lesson applied going forward:** an expected-error assertion must contain only the statement
under test. Setup that can fail with the same error belongs outside it.

## Stage 3 (import), 2026-10-03

### D-050: Non-EUR rows are rejected at import, in all three files
**Status:** accepted (tightens design §3, which put the EUR check at run time for the CSVs)
**Decision:** The ledger and settlement parsers reject any row whose currency is not EUR, as
the bank parser already does (design §7.3). The whole file is rejected, naming the line.
**Rejected:** Importing non-EUR rows and failing the run later. A file that Level 1 can never
reconcile would sit in the database looking usable; rejecting at the door says why
immediately, against the file that caused it.

### D-051: The camt.053 parser reads structure first, then applies Level 1 rules
**Status:** accepted
**Decision:** `read_camt053` extracts the design §7.3 elements for any currency and any shape the
official XSD allows, keeping amounts as the decimal text in the file. `apply_level1_rules` then
enforces Level 1 (one statement, EUR, booked, no reversals, batch bookings, multi-transaction
entries or per-entry charges, cent precision, balance tie-out) and only then converts amounts
to minor units.
**Why:** (1) The official ISO sample (S2) is in SEK and contains a batch-booked entry. The
two-step split lets a test prove the parser reads the official sample's values correctly and
also that Level 1 rejects it, for the right reason. (2) Converting text to minor units needs
the currency's ISO 4217 exponent, and only EUR's is sourced (S6). Converting after the
currency check means no unsourced exponent is ever used.
**Rejected:** A single pass that rejects as it reads: it could not be tested against S2.

### D-052: A rejected file is a recorded parse outcome, not a failed job
**Status:** accepted
**Decision:** When a parser rejects a file, the job is `done` and `import_parse` records
`rejected` with the reason. The job is `failed` only for unexpected errors (database or
infrastructure), which roll the parse back completely.
**Rejected:** Marking the job failed on rejection: it would confuse "this file is wrong" (a
business answer, final) with "the worker broke" (an operational problem, retryable).

### D-053: Repeated `RmtInf/Ustrd` elements are joined with one space
**Status:** accepted
**Decision:** `Ustrd` is 0..n in the XSD. The parser stores them joined by a single space,
and reference matching then compares that string exactly.
**Rejected:** Keeping only the first occurrence, which would silently discard bank-supplied
text. No generated file uses more than one, and the rule is recorded so that matching on
real files is predictable.

### D-054: Synthetic staff are seeded by a command, not by a migration
**Status:** accepted
**Decision:** `python -m recon seed-staff` creates "Demo Analyst 1", "Demo Analyst 2" and "Demo
Controller" (`is_synthetic = true`), idempotently.
**Rejected:** A seed migration: migrations define structure, and demo people would then exist
in every database the schema is applied to.

### D-055: CSV fields are stripped of surrounding whitespace; nothing else is normalised
**Status:** accepted
**Decision:** Leading and trailing whitespace is removed from every CSV field. Case, inner
spacing and characters are kept as written.
**Rejected:** (a) Rejecting files with padded fields, which is too brittle for spreadsheet
exports while adding no safety. (b) Wider normalisation such as case-folding references,
which would create matches the source data does not support.

### D-056: Test transactions are opened explicitly, because psycopg commits idle blocks
**Status:** accepted (found while writing the importer tests)
**Finding:** In psycopg 3, `conn.transaction()` on an *idle* connection begins **and commits**
a real transaction, even with autocommit off. The importer tests called the importer first
thing, so its writes committed and leaked into later tests ("already exists" failures). The
stage 1 tests were unaffected only because each happened to execute a plain statement first,
which opened an implicit transaction.
**Decision:** The `conn` fixture now runs a statement and asserts the connection is inside a
transaction before yielding. A guard test asserts that, after the importer runs, the
connection is still inside the uncommitted test transaction. With the fixture fix removed,
that guard test fails (checked).
**Rejected:** Relying on test order or on each test's first statement, which is what hid the
problem in stage 1.

## Stage 4 (matching engine), 2026-10-03

### D-057: Dates in explanations use a fixed month table, not strftime("%b")
**Status:** accepted
**Decision:** Explanations write dates as "30 Sep 2026" from a constant month-name table.
**Rejected:** `strftime("%b")`, whose output depends on the process locale. The same inputs could
then produce different explanation text, and therefore different result bytes, on a machine
with another locale. That would break replay (design §6).

### D-058: A payout reference with conflicting dates or ids fails the run
**Status:** accepted
**Decision:** If the lines sharing a payout reference disagree on `payout_date` or `payout_id`,
the engine raises `EngineInputError` and the run is recorded as `failed` with that reason.
**Rejected:** Taking the earliest date, or splitting the group. Each would make the engine guess
which of the processor's two statements is true, and windows and timing depend on the date.

### D-059: Leftover payouts are diagnosed with a looser lookup, in a fixed priority
**Status:** accepted (refines design §5.7 step 1)
**Decision:** After the passes, an unmatched payout group is re-examined with ledger rows found
by reference only (card, unallocated, any amount or date) and a bank credit found by reference
only (any date). The first condition that holds sets the reason:
(1) no credit → `timing` if inside the D-048 window, else `missing_from_bank`;
(2) credit ≠ Σ net → `amount_mismatch` (states gross, stated fees, deposit, unexplained rest);
(3) a ledger amount ≠ its line's gross → `amount_mismatch`;
(4) a line with no ledger entry → `missing_from_ledger`, naming it;
(5) amounts agree but a date is outside its window → `timing`, stating which date and the deadline.
**Rejected:** Reusing the strict pass rules for diagnosis: a group that failed *because of* a
date or an amount would come back with "nothing found", and the explanation could not say
what actually disagreed.

### D-060: Right amounts, wrong dates, are classified `timing`
**Status:** accepted
**Decision:** A payout or transfer whose counterpart exists with the right amount but was booked
outside its window is `timing`. The explanation names the dates and the window's deadline.
**Rejected:** `amount_mismatch` (untrue) or `missing_from_bank` (untrue: the money is there).
The reason list is fixed by the schema; `timing` is the truthful one, and the explanation
carries the specifics.

### D-061: Runs are recorded before they are computed
**Status:** accepted
**Decision:** The `reconciliation_run` row is committed as `running` before the engine starts.
Matches, exceptions, allocations and the transition to `finished` then go in one transaction.
On an engine input error, a parser-version mismatch or a database refusal, that transaction
rolls back and the run is marked `failed` with the reason. A reconcile job is `done` when its
run is recorded, whether the run finished or failed (as D-052 for parse jobs).
**Rejected:** Inserting the run only on success: a refused run would leave no trace, and "the
database refused the engine's output" is precisely the event an auditor would want to see.

### D-062: Bank-side references are trimmed of spaces only, like SQL `btrim`
**Status:** accepted
**Decision:** `EndToEndId` and `Ustrd` are compared after removing leading and trailing spaces
(U+0020) only.
**Rejected:** Python's default `strip()`, which also removes tabs and newlines. The engine and the
database finish check (which uses `btrim`) would then disagree on what "the same reference"
means, and a run the engine considers valid could be refused by the database.

### D-063: The engine's git commit is recorded from `GITHUB_SHA` when available
**Status:** accepted
**Decision:** The CLI sets `reconciliation_run.engine_git_sha` from the `GITHUB_SHA` environment
variable (set by GitHub Actions) and leaves it empty otherwise. It is provenance on the run
record only, never part of the canonical result.
**Rejected:** Running `git` from the engine, which makes the engine depend on its environment.

### D-064: Mutation check of the matching rules, and the coverage it added
**Status:** accepted (owner request for stage 4)
**Method:** In a throwaway copy of the repository (never the original), 18 mutations of
`worker/recon/engine.py` were applied one at a time. Each was an exact textual replacement that
had to match exactly once. The full suite was run after each, and the failing tests were
recorded. The baseline in the copy was clean (361 passed). Harness: committed at
`tools/mutation_check/mutate_engine.py`, with a README listing each mutation, how to run it
and the expected result (every mutation caught). It is a reviewer's tool, not a CI gate,
because it runs the full suite once per mutation. *(Updated 2026-10-03 at the owner's request:
originally a scratch script, not committed.)*
**Result:** no mutation survived, and each broke only tests about the rule it changed:

| Mutation | Tests that failed |
|---|---|
| M01 gross_net: 1-cent tolerance | fee-difference cases −1 and +1 cent |
| M02 many_to_one: accept a partial batch | all-or-nothing batch |
| M03 exact: drop the date window | outside-window timing; bank before ledger date; deadline sweep |
| M04 duplicate labelled missing_from_bank | demo P4; demo exception set; P4 explanation; card and transfer duplicate tests |
| M05 timing: old rule (payout_date > period_to) | D-048 (a); D-048 (b); deadline sweep |
| M06 timing: no deadline in explanation | D-048 (a); D-048 (b); demo deadlines; deadline sweep |
| M07 exact: ignore the amount | transfer one cent short |
| M08 ledger/processor window widened to 2 days | ledger two days off |
| M09 payout credit: accept debits | payout credit must be money in |
| M10 payout credit: drop the window | payout window (day 11, day 16); deadline sweep |
| M11 tie-break: last candidate instead of first | earliest of two identical credits |
| M12 deposit/debit labels swapped | unknown deposit/debit; demo P5; demo exception set; and three other scenarios with leftovers |
| M13 many_to_one: drop deposit == Σ net | batch one cent short |
| M14 exact: drop the reference check | different reference |
| M15 ledger order reversed | demo P4; card and transfer duplicate tests |
| M16 ledger timing boundary `>=` | transfer and card boundary cases (missing side) |
| M17 inconsistent payout dates accepted | engine refusal test; persisted failed-run test |
| M18 duplicate ignores the amount | same reference, different amount |

**Added because of it:** eight mutations were caught by a single engine test. For the six whose
rule the database finish check also enforces (M01, M02, M07, M09, M13, M14), six persisted
near-miss runs were added (`test_near_miss_is_persisted_as_an_exception_and_the_run_finishes`).
Re-run, each of those six mutations now fails two independent layers: its engine test, *and*
the database refusing the run. Confirmed for M01 by the failure text: "match #1 does not
satisfy the gross_net rule".
**Not doubled, by nature:** M08, M11 and M18 concern date windows, tie-breaks and duplicate
labelling, which the database finish check does not judge (it checks arithmetic, shape,
references and currency). Their single engine tests are the intended coverage.

## Stage 5 (exceptions queue), 2026-10-03

### D-065: The queue is a read-only view; views are read-only for every role
**Status:** accepted
**Decision:** Migration 0007 adds `exception_queue`: one row per exception with its status
(`open` until a first resolution exists, then `resolved`), the resolution in force, who made it
and when, and how many resolutions it has had. Both roles get SELECT only. The same migration
revokes the worker's INSERT on the `current_resolution` view: 0006's blanket grant had reached
that view, which PostgreSQL makes auto-updatable, so it was a second path for inserting
resolutions. The role matrix test now states that views are SELECT-only.
**Rejected:** (a) A `status` column on `run_exception`: it would have to be updated, and that
table is append-only. (b) Computing status in application code: the web (stage 7) and the CLI
would each need their own copy of "which resolution is in force".

### D-066: Corrections name what they supersede; the database decides races
**Status:** accepted
**Decision:** `correct(exception, supersedes_id, ...)` inserts a resolution superseding the one
the caller saw. If a colleague corrected it meanwhile, `UNIQUE (supersedes_id)` refuses the
second correction as stale, naming the resolution now in force. Two simultaneous first
resolutions are decided the same way, by the partial unique index (one root per exception).
Both races are tested with two real connections, the second confirmed to be waiting on the
first's lock before the first commits.
**Rejected:** (a) "Correct whatever is current" without naming it: a reviewer could overwrite a
correction they never saw. (b) Application-level locking (`SELECT ... FOR UPDATE`): it adds a
second mechanism for something the constraints already guarantee.

### D-067: Database refusals are translated by constraint name, never re-implemented
**Status:** accepted
**Decision:** `recon.resolutions` does not pre-validate notes, codes or chains. It inserts, and
maps each refusal to a plain message by the constraint that fired (`resolution_note_check`,
`resolution_one_root_per_exception`, `resolution_superseded_once`, ...). The names were read
from the database catalog, not assumed. An unrecognised error is re-raised unchanged, so a new
failure mode cannot be mislabelled as a known one.
**Finding:** the finished-run trigger (0004) fires before the foreign key on `exception_id`, so
a non-existent exception arrived as "run missing / not finished". The translation checks which
case it is and says "no exception with id N".
**Rejected:** Mirroring the rules in Python for nicer messages: two copies of a rule drift, and
the database copy is the one that is enforced.
