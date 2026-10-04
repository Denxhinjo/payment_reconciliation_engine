# Known fragilities

Weaknesses this project carries **on purpose**. Each one is a decision with a record, not
something that quietly accumulated. Every entry says what it is, why it is acceptable now, and
what would make it stop being acceptable. When that trigger happens, the entry is fixed or
re-decided, never silently kept.

This is a living list: entries are updated, and closed entries move to "Closed" with the commit
that closed them. The append-only history of *why* lives in `docs/decisions.md`. Started
2026-10-04 (before stage 7), after two items had appeared as "noticed, not done" in consecutive
stage reports.

| # | Fragility | Area |
|---|---|---|
| F1 | Exceptions-queue tests find exceptions by suggested reason | tests |
| F2 | No command or UI to requeue a failed job | operations |
| F3 | Bank remittance text must match references exactly | matching |
| F4 | The parser has no file-size limit of its own | import |
| F5 | Resolutions do not carry over to a later run of the same files | exceptions |
| F6 | Demo sign-in: anyone can act as any synthetic staff user; no separation of duties | auth |
| F7 | Append-only is enforced by triggers a database owner could drop | integrity |
| F8 | The engine trusts the fee each settlement line states | matching |
| F9 | Date windows are calendar days, without a bank-holiday calendar | matching |
| F10 | A payout missing in the last 3 days of a period is reported as `timing` | matching |
| F11 | End-to-end role tests skip where the test login cannot `SET ROLE` (e.g. Neon) | tests |
| F12 | Time-zone determinism subprocesses skip on Windows; only CI runs them | tests |
| F13 | Only the current engine version's golden hash is checked by CI | replay |
| F14 | No static guard against floats in the engine's *intermediate* arithmetic | determinism |
| F15 | `engine_git_sha` is only recorded when `GITHUB_SHA` is set | provenance |
| F16 | The generator's planted-problem rules are only verified for September 2026 | demo data |
| F17 | The mutation check (30+ min) is a manual reviewer tool, not a CI gate | tests |

---

### F1: Exceptions-queue tests find exceptions by suggested reason
**What:** `tests/test_exceptions_queue.py` locates the demo exceptions (P1, P2, P4, P5) through a
fixture keyed on `suggested_reason`. A change in labelling makes those 19 tests **error** during
setup rather than fail on an assertion.
**Why acceptable now:** labelling is pinned twice, by the engine tests and the golden hash, so a
relabel cannot go unnoticed. Since stage 6 the mutation tool reports errors as well as failures.
**Stops being acceptable when:** a queue test is ever the *only* thing that would catch a
regression. Then it must locate exceptions by their evidence rows (entry ids, payout ids), so it
fails with a message instead of erroring.

### F2: No command or UI to requeue a failed job
**What:** a job that fails stays failed (noticed in stage 3).
**Why acceptable now:** failures are recorded with their reason, nothing retries silently, and
the owner deferred requeueing to the UI in stage 7.
**Stops being acceptable when:** stage 7 ships. **Being addressed in stage 7.** Requeue must be
idempotent and must preserve `attempts` (poison-pill protection).

### F3: Bank remittance text must match references exactly
**What:** direct transfers match on `RmtInf/Ustrd` equal to the ledger reference after trimming
spaces. Repeated `Ustrd` elements are joined with one space (D-053). Real banks sometimes wrap,
truncate or prefix this text (noticed in stage 3).
**Why acceptable now:** exact matching is predictable, and an unmatched transfer surfaces as an
exception, never as a wrong match. All data is synthetic and the format is ours.
**Stops being acceptable when:** real bank files (or a published bank's usage guide) are in
scope. Normalisation rules then need a cited source and their own tests.

### F4: The parser has no file-size limit of its own
**What:** the parsers trust the database's 4 MiB limit on `import_file.raw` (noticed in stage 3).
**Why acceptable now:** every byte the parsers see comes from that column. Nothing else feeds
them.
**Stops being acceptable when:** any path parses bytes that did not come from `import_file`
(e.g. a direct upload-to-engine call).

### F5: Resolutions do not carry over to a later run of the same files
**What:** a resolution belongs to one run's exception (D-021). Re-running or replaying creates
new, open exceptions. Fingerprints are stored to make carry-over possible later.
**Why acceptable now:** Level 1 has no period close, and replays exist to compare results, not
to be worked.
**Stops being acceptable when:** period close or "re-run after a fix" becomes a workflow; staff
would then redo resolved work.

### F6: Demo sign-in, and no separation of duties
**What:** the web UI signs in through a "sign in as" picker over synthetic staff (D-031), with no
passwords. Anyone can act as anyone, and the same person may resolve and then correct their own
resolution.
**Why acceptable now:** this is a public demo on synthetic data; the picker is labelled as such
and the accounts are printed openly.
**Stops being acceptable when:** any non-synthetic data, or any real user, is involved. That
requires real authentication (SSO) and a preparer ≠ approver rule.

### F7: Append-only is enforced by triggers a database owner could drop
**What:** triggers and role grants stop the application from editing history, but the table
owner can drop a trigger (design §13).
**Why acceptable now:** the threat being addressed is application bugs and misuse through the
app, not a hostile DBA.
**Stops being acceptable when:** tamper *evidence* is a requirement (e.g. an external audit).
Then add a hash chain over resolutions and runs, with the chain head exported somewhere the DBA
does not control.

### F8: The engine trusts the fee each settlement line states
**What:** gross-vs-net matching checks the stated fee, not the contracted price list (design §13).
A processor overcharging consistently would reconcile cleanly.
**Why acceptable now:** fee schedules are reference data that need a cited source; Level 1 has
none.
**Stops being acceptable when:** a fee schedule is available as a sourced input.

### F9: Calendar-day windows, no bank-holiday calendar
**What:** windows count calendar days (D-016); the 3-day payout window absorbs weekends but not
holidays.
**Why acceptable now:** a holiday calendar is reference data needing a source, and no Level 1
case needs it.
**Stops being acceptable when:** real statements around holidays produce timing exceptions that
a calendar would have prevented.

### F10: A payout missing in the last 3 days of a period is reported as `timing`
**What:** the accepted D-048 trade-off. The explanation states the deadline after which it
should be treated as missing.
**Why acceptable now:** the deadline is explicit on every such exception.
**Stops being acceptable when:** period close exists. The next period's run must then escalate
past-deadline timing items automatically.

### F11: End-to-end role tests skip where the test login cannot SET ROLE
**What:** two end-to-end privilege tests skip on servers where the test connection may not
`SET ROLE` (Neon's owner, PostgreSQL 16+). The catalog matrix test covers the same guarantee
everywhere (D-039).
**Why acceptable now:** the guarantee is still tested on every server, and the skip says why.
**Stops being acceptable when:** the catalog test is ever removed or weakened.

### F12: Time-zone determinism subprocesses skip on Windows
**What:** Windows does not apply IANA names in `TZ`, so the two time-zone cases skip locally on
Windows with a reason naming the offset actually in effect. CI (Linux) runs them strictly and
fails if they do not apply (D-071).
**Why acceptable now:** CI is the authoritative run and cannot pass without them.
**Stops being acceptable when:** CI stops running on Linux, or the strict setting is removed
from CI.

### F13: Only the current engine version's golden hash is checked
**What:** CI checks the golden entry for the running `ENGINE_VERSION`. Older entries are only
reproducible by checking out their tag (D-069).
**Why acceptable now:** there is one engine version.
**Stops being acceptable when:** a second version ships. Then a CI job should replay the demo
month at each tagged version.

### F14: No static guard against floats in intermediate arithmetic
**What:** verified on 2026-10-04: the whole money path is integer minor units, with no float,
`Decimal` or true division in any engine module. But the only enforcement is the serialiser,
which refuses floats that reach the *output*. A float used only inside a comparison would not
be caught.
**Why acceptable now:** a static scan and a runtime type check both found none (stage 7
pre-check).
**Stops being acceptable when:** the next engine change lands. The purity test (D-070) should
then also forbid float literals, `float()`, `Decimal` and true division in engine modules.
*Found during the stage 7 pre-check; recorded rather than fixed, per the stage's scope rule.*

### F15: engine_git_sha is only recorded from GITHUB_SHA
**What:** runs made outside GitHub Actions record no commit (D-063).
**Why acceptable now:** the engine version is always recorded, and each version is tagged.
**Stops being acceptable when:** runs are produced by a deployed worker whose environment does
not set it. The deploy must then set the variable.

### F16: Planted-problem rules only verified for September 2026
**What:** the generator chooses planted days by weekday rules; the tests that prove the planted
set equals the discrepancies run on the committed 2026-09 month only.
**Why acceptable now:** the demo is that month.
**Stops being acceptable when:** a second demo month is generated or published.

### F17: The mutation check is a manual tool
**What:** `tools/mutation_check/` runs the suite once per mutation (30+ min), so it is not in
CI (owner decision).
**Why acceptable now:** it was run in full at `f27bd12` with 18/18 caught, and the results are
recorded.
**Stops being acceptable when:** `engine.py` changes. The tool must be re-run before the change
is merged and the result recorded in its README.

---

## Closed

*(none yet)*
