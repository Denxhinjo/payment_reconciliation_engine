# Payment reconciliation demo: design rationale

*For a Head of Compliance, Finance or Operations. Reading time about 10 minutes; the technical
appendix at the end is for engineers. Written 10 October 2026, describing the demo as it runs at
https://payment-reconciliation-demo.vercel.app since that day.*

Each claim below cites its evidence: an automated test that checks it (*test: name*), or an
entry in the project's decision log, `docs/decisions.md` (*decision: D-nnn*). Where a property
holds only within limits, the limit is stated next to it, not in a footnote.

## 1. What this is

This is a working demonstration of a three-way payment reconciliation. It matches a company's
own ledger against its payment processor's settlement report and its bank statement, and it puts
every item that does not agree in front of a person, with the evidence. It keeps a permanent
record of what it found and what people decided, and it can recompute any past reconciliation
exactly. **All data in it is synthetic**: "Orrery Payments" (the processor) and "Demo Bank" are
fictional, and no real people, accounts or transactions are used anywhere.

## 2. Why three-way reconciliation

Each source records a different part of the same money's journey:

- **The ledger** is the company's own record: what it believes customers paid, payment by
  payment.
- **The settlement report** comes from the payment processor. It lists each payment it handled,
  the fee it kept, and which payout each payment was bundled into.
- **The bank statement** comes from the bank. This demo uses camt.053, a standard file format
  in which banks send a daily statement of what actually arrived in the account. It is the only
  source that shows cash received.

A two-way match leaves blind spots. The demo month contains five deliberately planted problems,
and three of them show what a two-way match would miss:

- **Ledger against processor only.** Both agree that payout PO-20260914 (9 payments, EUR 1,752.23)
  was paid. Only the bank statement shows the money never arrived. *(Planted problem P1.)*
- **Ledger against bank only.** The ledger says EUR 316.85 and the bank received EUR 311.76.
  Without the settlement report you cannot tell how much of the gap is the processor's fee
  (EUR 4.69) and how much is a real shortfall (EUR 0.40). *(P2.)*
- **Ledger against bank only, again.** One bank credit of EUR 10,624.87 pays for 50 separate
  card payments. Only the processor's report says which 50. *(P3.)*

The other two planted problems are a payment recorded twice in the ledger (P4), and a EUR 250.00
deposit nobody expected (P5). A test re-derives every disagreement in the month without reading
the list of planted problems. It requires the result to be exactly those five, plus three
expected timing differences at month end (*test:
`test_files_disagree_exactly_where_planted`; decision: D-043*).

On the demo month, the system makes 65 matches and raises 7 exceptions: P1, P2, P4, P5 and the
three timing items. Each exception is a **break**, an item that does not match, routed to a person
(*decision: D-090; verified on the live demo, decision: D-094*).

## 3. What the system guarantees

### 3.1 The same file cannot be imported twice

**Promise.** If someone uploads a file that has already been imported, under any name, nothing new
is created. The system says which existing file it is.

**How.** When a file arrives, the database computes its **SHA-256 hash**: a 64-character
fingerprint of the file's exact bytes. Two different files will in practice never share one. The
database refuses a second file with the same fingerprint. The fingerprint is calculated by the
database itself, so the uploading program cannot supply a false one. Files are stored exactly as
received and can never be altered afterwards.

**Evidence.** *test: `test_same_bytes_imported_twice_is_rejected`,
`test_reimporting_the_same_bytes_does_nothing`, `test_sha256_cannot_be_supplied_by_the_caller`;
decision: D-005, D-006.*

### 3.2 Every row is accounted for, and no payment is matched twice

**Promise.** In each reconciliation, every ledger row, settlement line and bank entry ends up in
exactly one place: one match, or one exception. A payment cannot satisfy two matches, and nothing
can silently drop out.

**How.** Each reconciliation run records where every input row went, and the database allows
each row at most one entry per run. Before a run may be marked finished, the database checks that
every row has been placed. It also recomputes each match's arithmetic itself: for example, that
gross minus the stated fee equals what the bank received. This is a second, independent check on
the matching program. If the two disagree, the run cannot finish.

**Limit.** "At most once" applies *within one run*. A later run of the same files starts afresh,
by design: that is what lets a run be replayed (3.5).

**Evidence.** *test: `test_a_row_cannot_be_consumed_by_two_matches_in_one_run`,
`test_a_row_cannot_be_both_matched_and_an_exception`, `test_unallocated_bank_row_blocks_finish`,
`test_gross_net_with_fee_mismatch_blocks_finish`,
`test_many_to_one_with_sum_mismatch_blocks_finish`; decision: D-010, D-012.*

### 3.3 Breaks go to an exceptions queue, and every decision is recorded

**Promise.** Every break appears in a queue with a plain-language explanation and the evidence
rows behind it. A person resolves it by choosing a reason and writing a note. The record shows who
resolved it and when, and it is never edited or deleted. A correction is a new entry that points at
the one it replaces, and both stay visible.

**How.**
- Each exception carries a suggested reason (for example `missing_from_bank`, `amount_mismatch`,
  `possible_duplicate`, `timing`) and an explanation. Timing items also state the date by which
  the money should have arrived.
- A resolution must use one of eight reason codes and include a note of at least 10 characters.
  The reason codes include "Processor error: claim raised with processor", "Funds identified and
  posted" and "Written off: below threshold".
- The database refuses any change or deletion of a recorded resolution.
- If two people resolve or correct the same item at the same moment, the database accepts exactly
  one. The other is told who got there first.

**Evidence.** *test: `test_resolving_marks_the_exception_resolved_with_who_why_and_note`,
`test_correction_supersedes_and_keeps_the_original_unchanged`, `test_short_or_blank_note_is_refused`,
`test_update_is_refused`, `test_delete_is_refused`,
`test_two_people_resolving_the_same_exception_at_once_one_wins`; decision: D-019, D-020, D-048,
D-066.*

**Limit, and it matters.** In this demo, "who" is whichever demo account the visitor chose at
sign-in. The sign-in page says so: *"These accounts are synthetic and public. There is no
password."* The record is only as trustworthy as the sign-in in front of it. There is also no
rule stopping the same person from resolving an item and then correcting their own resolution.
Both need real authentication and a preparer/approver rule before any real use (decision: D-031,
D-073; known weakness F6, see section 6).

### 3.4 Jobs have a fixed attempt budget; only a controller can requeue

Background work, such as reading an uploaded file, running a reconciliation or replaying one,
is done by **jobs**. A job is a task written to a **queue** (a list of waiting tasks) and picked up
by the **worker**, a separate program that runs on a schedule.

**Promise.**
- Each job gets at most 3 attempts.
- An attempt counts the moment the worker starts it, so a crash still uses it up.
- A controller can put a failed job back in the queue (**requeue** it), but that never restores
  used attempts.
- A job that has used all 3 can't be requeued at all, so a file that crashes the worker every time
  cannot loop forever.
- Analysts cannot requeue.
- Every requeue is logged: who, when, how many attempts had been used, and the error it replaced.

**How.**
- The attempt count goes up when the worker claims the job, before any work starts.
- Requeueing is a single database function. It refuses anyone who is not a controller, refuses a
  job at its budget, and never touches the attempt count.
- Pressing requeue twice has the same effect as pressing it once.
- The requeue log can't be edited.

**Evidence.** *test: `test_requeue_preserves_attempts`,
`test_a_job_at_its_attempt_budget_cannot_be_requeued`, `test_the_worker_never_claims_a_job_at_its_budget`,
`test_an_analyst_is_refused_by_the_database`, `test_analyst_requeue_is_refused_with_403_and_changes_nothing`,
`test_requeue_logs_who_when_and_the_error_it_replaced`, `test_two_simultaneous_requeues_produce_one`;
decision: D-073, D-074.*

### 3.5 Any run can be replayed, and the result is proved identical

**Promise.** A reconciliation from months ago can be recomputed from the original files. The system
proves that the result is the same, byte for byte, or records plainly that it is not.

**How.**
- The matching program uses nothing but the three stored files. It never reads the clock, the
  database, random numbers or the machine's settings; a test inspects the program's code for any
  of these.
- Each run stores its result and that result's fingerprint (SHA-256, see 3.1).
- A replay is recorded as a new run, linked to the original, and shown as *identical* or
  *different*. A difference is kept as evidence, never discarded.
- For the demo month, the expected fingerprint is fixed in advance for each version of the
  matching program: `4962e880…67c7` for version 1.0.0.
- Every daily reset of the public demo checks that run #1 still has that fingerprint, and that its
  replay is identical.
- A run made by an older version of the program is replayed with that version, which is kept under
  a fixed label in the code history.

**Evidence.** *test: `test_replay_reproduces_the_run_byte_for_byte`,
`test_demo_month_result_equals_the_golden_hash_for_this_engine_version`,
`test_a_differing_replay_is_recorded_not_refused`, `test_engine_modules_have_no_clock_randomness_environment_or_io`,
`test_a_fresh_interpreter_reproduces_the_golden_bytes`; decision: D-007, D-068, D-069, D-070,
D-089.*

### 3.6 Every job records who asked for it

**Promise.** Every queued job names the person who requested it:
- the person who uploaded the file;
- the person who asked for the reconciliation or the replay.

Once recorded, the name can't be changed, including by the worker that does the job. The demo's
starting data is attributed to a clearly labelled **system actor**, "Deployment seed", not to a
demo person, because no person did that work. The system actor cannot sign in.

**How.** Every job must name a requester, and the database refuses any later change to it. The
website and the seeding tool both create jobs through the same function in the database, so there
is one way to enqueue work, not two that could drift apart. The jobs list, the file list and the run
history show the requester and mark the system actor "system actor".

**Evidence.**
- *test: `test_enqueued_jobs_keep_their_requester_through_the_worker`,
  `test_the_requester_cannot_be_changed_by_anyone`,
  `test_an_upload_s_parse_job_records_the_uploader`,
  `test_a_web_replay_records_the_signed_in_user_and_the_worker_does_not_change_it`,
  `test_posting_the_system_actor_s_id_to_the_sign_in_endpoint_is_refused`;
  decision: D-091, D-092.*
- *Checked on the live site on 10 October 2026: a reconciliation queued as Demo Analyst 1 appeared
  as requested by Demo Analyst 1 (decision: D-094).*

**Limits.**
- Runs made with the developer command-line tool, outside the job queue, have no requester (known
  weakness F26).
- Only the website refuses the system actor. Someone with direct database access could still act
  in its name (F27).

## 4. When something fails

**A bad file.** A file that cannot be read, or that breaks a rule (for example, a currency other
than EUR, or a bank file that does not follow the official camt.053 definition), is **rejected**.
It is kept as evidence, with the reason written down, and it can never be used in a reconciliation.
A rejected file is a recorded outcome, not a crash.
*(test: `test_rejected_file_is_a_recorded_outcome_with_no_rows`,
`test_rejected_file_cannot_be_used_in_a_run`; decision: D-003, D-050, D-052.)*

**A run the database refuses.** The run is recorded before any matching starts. If matching fails,
or the database's own arithmetic check disagrees, the run is kept and marked *failed* with the
reason. It stays in the run list; it never silently disappears.
*(test: `test_engine_input_error_records_a_failed_run`,
`test_a_failed_run_is_listed_as_failed_with_its_reason`; decision: D-061, D-078.)*

**A worker crash.** When the worker takes a job, it holds the job for 15 minutes. If it dies
mid-job:
- the next worker run first marks any such abandoned job *failed*, with "lease expired: worker did
  not finish";
- the attempt still counts;
- nothing half-done is kept, because a job's work and its completion are saved together or not at
  all.

*(test: `test_a_stale_running_job_is_reclaimed_as_failed_with_attempts_preserved`,
`test_a_worker_whose_lease_lapsed_mid_parse_commits_nothing`, `test_every_worker_invocation_sweeps_first`;
decision: D-081.)*

**A job that uses its whole budget.** After 3 attempts the worker never picks it up again, and
requeueing is refused. It stays visible on the Jobs page with its error and "3 / 3" attempts, and
it needs a person to investigate (3.4). There is deliberately no button that grants a fresh budget
(known weakness F20).

**A worker that stops running.** Every queued job shows when the worker is expected next. After 70
minutes it says instead: *"Queued for over 70 minutes: the worker should have run by now. It may be
paused or failing…"*. A stopped worker is visible, not silent. *(decision: D-088.)*

**Repeated clicks.** Asking twice for the same reconciliation or replay while one is waiting creates
one job, not two. *(test: `test_reconcile_request_is_queued_once`; decision: D-075.)*

## 5. What broke during development, and how it was fixed

All of these are recorded in the decision log. None was found in production data; there is none.

- **Twelve permission tests passed without testing anything** (decision: D-049). On the hosted
  database, the tests' setup step failed with the same error the tests were waiting for. The tests
  therefore "passed" for the wrong reason. A different test that expected success failed, and that
  exposed the problem. They were replaced by a full check of every permission against an explicit
  list, which was then shown to catch a deliberately wrong permission.
- **Test data leaked between tests** (decision: D-056). The database library silently committed
  work that the tests meant to throw away. Fixed in the test setup, with a guard test that fails if
  the problem returns.
- **Two "different environment" checks proved nothing** (decision: D-071). A German-language check
  never actually ran in German. On Windows, two time-zone checks ran in the default time zone. The
  owner flagged it: the tests reported success for checks that had not run. Each check now confirms
  its environment is in effect. Otherwise it is skipped with the reason, and on the automated build
  server it fails.
- **The deliberate-sabotage tool missed a class of failures** (decision log, stage 6 note). The
  tool breaks matching rules on purpose and confirms the tests notice. It listed failed tests but
  not tests that crashed. It now reports both.
- **A wrong refusal message** (decision: D-067). Resolving an exception that did not exist said
  "run missing or not finished". Now it says the exception does not exist.
- **A file saved with the wrong line endings** (decision: D-084). A database change file was saved
  in Windows format on one machine. The database-update tool would then have refused the database,
  because the file's fingerprint no longer matched. A test now rejects such files.
- **The first public deployment failed to build** (decision: D-090). The hosting service did not
  recognise the type of application. Nothing went live; it was fixed in the project's configuration.
- **Caught before it broke** (decision: D-094). The first plan for moving the public demo onto
  the new starting data relied on a database-host feature that is not available for this setup. It
  was caught from the host's documentation before anything ran. A safer plan was used instead,
  with an instant way back to the previous state.

Two known weaknesses were closed before the demo went public:
- a job whose worker died would have stayed "running" forever (now the 15-minute hold above);
- the website's confirmation messages could be forged through a crafted web address (now only
  fixed texts are shown).

*(known weaknesses F19 and F18, `docs/known-fragilities.md`; decision: D-081, D-082.)*

## 6. What this demo deliberately does not do

The project keeps a list of these weaknesses, each with the condition that would make it
unacceptable (`docs/known-fragilities.md`). The ones a finance or compliance reader should know:

- **No real sign-in and no separation of duties.** Anyone can act as any demo account, and one
  person can both resolve and correct (F6).
- **No tamper evidence against the database's owner.** The rules that make records permanent stop
  the application, but whoever administers the database could remove them. Tamper evidence (a chain
  of fingerprints exported elsewhere) is not built (F7).
- **Resolutions don't carry over.** A decision on one run's break does not follow the same break
  into a later run of the same files (F5).
- **The processor's fee is taken as stated** in its report, not checked against a fee schedule (F8).
- **Matching rules are simple.** Date windows count calendar days, with no bank-holiday calendar
  (F9). References must match exactly (F3). Only EUR is accepted (D-050). Only camt.053 version
  .001.02 is read (D-002).
- **The daily reset time is not guaranteed.** The public demo discards visitors' changes once a
  day. It is scheduled for 03:00 UTC, but the scheduler (GitHub Actions) does not guarantee start
  times. The reset on 9 October started at 09:57 UTC, and the one on 10 October at 09:20 UTC
  (decision: D-093; GitHub Actions run history). The banner therefore says "usually around 03:00
  UTC".
- **The worker runs hourly, and that time is not guaranteed either.** Queued work waits for it, and
  there is no "run now" button (F25, D-087).
- **It makes no regulatory claim.** It records and enforces what is described above. Whether that
  satisfies a given regulator's requirements is for the firm to judge.

---

## Appendix: technical notes for engineers

**Stack.**
- PostgreSQL 18, with raw SQL migrations 0001–0012 and no ORM.
- Python 3.12 worker: psycopg 3, lxml.
- Next.js 15 web UI, which only reads views and computes no figures (D-076).
- Hosting: Neon (database), Vercel (`fra1`) and GitHub Actions (worker hourly at minute 7, reset
  scheduled 03:00 UTC, CI).
- CI: 645 tests passed on commit `6dfd0a6`, against real PostgreSQL. Database tests fail rather
  than skip without a database (D-027, D-036).

**Money.** `bigint` minor units with an explicit ISO 4217 currency code on every amount; never
floating point (D-001).

**Matching passes, in fixed order** (design §5).
- `exact`: a ledger bank transfer against a bank credit with the same reference and amount,
  within a window.
- `gross_net`: a single-payment payout. The ledger gross minus the bank amount must equal the
  stated fee.
- `many_to_one`: a batched payout grouped by the processor's payout reference. All or nothing,
  with zero tolerance (D-013, D-014).
- Leftover rows are classified into seven suggested reasons (D-017, D-018).

**Integrity in the database.**
- Append-only triggers on raw files, parsed rows, runs and resolutions (SQLSTATE RC001).
- Run state machine (RC002), and a finish check that re-verifies allocations and arithmetic
  (RC003, D-012).
- Allocation tables keyed `(run_id, row_id)`, with composite foreign keys to the run's own files
  (D-010, D-011).
- Resolution chain: one root per exception and `UNIQUE (supersedes_id)` (D-019, D-066).
- Requester is immutable (RC006) and never invented by the migration (RC007) (D-091).
- Roles: `recon_web` and `recon_worker`, each with an exact privilege matrix, tested from the
  catalog (D-039).

**Jobs.**
- Attempts are incremented at claim. The budget is `recon_max_job_attempts()` = 3.
- 15-minute lease, with a sweep `expire_job_leases()` at the start of every worker run (D-081).
- `requeue_failed_job()` is SECURITY DEFINER, controller only and idempotent, and logs to
  `job_requeue` (D-074).
- Partial unique indexes allow one active replay per run and one active reconcile per file triple
  (D-075).
- Enqueueing goes through `upload_file()`, `enqueue_reconcile()` and `enqueue_replay()`, shared by
  the web and the worker (D-091).

**Replay.**
- The engine is a pure function of the raw bytes and `ENGINE_VERSION`, checked by a static
  syntax-tree test and by fresh interpreters with varied hash seed, time zone and locale (D-070,
  D-071).
- Golden result hashes per engine version live in `worker/tests/golden/results.json`, which is
  append-only by policy. Git tag `engine-v1.0.0` (D-069).

**camt.053.** Validated against the official ISO 20022 XSD, whose SHA-256 is pinned, with a
hardened parser that refuses a DTD (D-003, D-047).

**Public deployment.**
- Branches:
  - `pristine-v2`: the reference, seeded through the job path by `recon seed-demo` in one
    transaction (D-092);
  - `live`: its child, reset daily from its parent;
  - `pristine` and `live-v1`: kept for rollback (D-094).
- The reset workflow verifies run #1's golden hash, an identical replay, 0 resolutions and 3 files
  after every reset (D-086, D-089).
- Runbook: `docs/deploy.md`.
