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
| F20 | No action grants a job a fresh attempt budget | operations |
| F21 | Web end-to-end tests share one server and database across files | tests |
| F22 | Sign-out is not origin-checked | web |
| F23 | Upload size is checked after the body has been read | web |
| F24 | The scheduled worker uses part of the free compute budget | operations |
| F25 | No "Run now": queued work waits for the hourly worker | operations |

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
**On the public deployment:** visitors' writes would be permanent (append-only). Mitigated by the
nightly reset of the `live` branch (D-085, D-086): writes last at most until 03:00 UTC. Still open:
anyone can act as any synthetic user, and abuse is visible until the reset.

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

### F20: No action grants a job a fresh attempt budget
**What:** a job at `recon_max_job_attempts()` (3) can never run again. By owner direction, a
fresh budget must be a distinct, explicitly named action, never a side effect of requeue. That
action does not exist.
**Why acceptable now:** the remedy for a job that failed three times is to fix the cause and
submit the work again (re-upload, or request a new reconciliation), which creates a new job.
**Stops being acceptable when:** an operator needs to retry the *same* job after a fix. Then add
`grant_fresh_attempts(job, staff, reason)`: controller only, with a mandatory reason, logged.

### F21: Web end-to-end tests share one server and database across files
**What:** `test_web_auth.py` and `test_web_flows.py` use one session-scoped server and database,
so one file's actions (e.g. a requeue) are visible to the other.
**Why acceptable now:** assertions are written relative to state captured before the action
(counts and rows before/after), and the suite passes in both file orders (checked).
**Stops being acceptable when:** a test has to assert absolute state. Give it its own seeded
database and server.

### F22: Sign-out is not origin-checked
**What:** `POST /api/session/end` clears the cookie without checking the Origin header, so a
cross-site page could sign a user out.
**Why acceptable now:** logging someone out exposes nothing and changes no data. SameSite=Lax
already stops cross-site POSTs from carrying the cookie for every action that matters.
**Stops being acceptable when:** sign-out does anything beyond clearing the cookie (e.g. revokes
server-side sessions).

### F23: Upload size is checked after the body has been read
**What:** `/api/upload` reads the whole multipart body and then refuses anything over 4 MiB. The
database enforces the same limit. A client can still make the server read a large body.
**Why acceptable now:** the route requires a signed-in session (an anonymous request gets 401
before the body is read), and the demo has no untrusted signed-in users.
**Stops being acceptable when:** the app is exposed to untrusted signed-in users. Then cap the
request size at the platform or proxy, or check `Content-Length` first.

### F24: The scheduled worker uses part of the free compute budget
**What:** Neon's free plan pauses an idle database after 5 minutes (cannot be disabled) and allows
100 CU-hours **per project** per month (Plans page, read 2026-10-07; compute has no account-wide
total, so the owner's other demo does not share this budget). Each hourly worker run wakes the
database for about 5 minutes, about 15 CU-hours a month; the nightly reset check adds about 0.6.
**Why acceptable now:** about 16 of 100 CU-hours before visitors (it was about 61 at every 15
minutes, before D-087).
**Stops being acceptable when:** visitor traffic plus the schedule approach 100 CU-hours (Neon then
suspends compute until the next month). Run the worker only when there is queued work.

### F25: No "Run now": queued work waits for the hourly worker
**What:** "Replay this run" and "Queue reconciliation" only enqueue a job. The worker processes it
at its next hourly run (minute 7), or when someone runs the workflow by hand in GitHub Actions. A
visitor may wait up to an hour to see a replay or an upload's parse result.
**Why acceptable now:** the page says "Replay pending" honestly, and the work is durable in the
queue. Nothing is lost by waiting.
**Stops being acceptable when:** the demo is shown live to someone waiting for a result. Then add a
"Run now" that triggers the worker workflow (`workflow_dispatch`), which needs a GitHub token with
"actions: write" stored in Vercel: a credential decision for the owner.
*Recorded 2026-10-07 (D-087), after the owner's request assumed such a button already existed.*

---

## Closed

### F18: Flash messages travel in the URL and can be crafted (closed before deployment)
Closed by D-082. Pages render only fixed messages looked up from a code (`?notice=` /
`?error=`), with at most a positive-integer `?ref=`. Unknown codes, built-in property names
(`constructor`, `__proto__`) and malformed references render nothing, and the type checker
rejects any route that passes free text. Tested over HTTP: crafted text in either parameter, on
four pages, signed in and out, never appears in the page markup. Breaking the lookup on purpose
fails 56 of those tests. Noted, not a fragility: Next.js serialises the URL's own query string
into its `<script>` router payload; it is never displayed, and it is the same text as the
address bar.

### F19: A job whose worker dies stays `running` (closed before deployment)
Closed by D-081. A claim takes a 15-minute lease (`lease_until`). Every worker invocation first
sweeps: jobs still `running` past their lease become `failed` ("lease expired: worker did not
finish"), with attempts preserved, so they can be requeued within the budget, and a job that
keeps dying stops at it. A worker whose lease lapsed cannot record its result (its parse rows
roll back). Tested: a stale job reclaimed; a job within its lease untouched; repeated expiry
stops at 3 attempts; the lapsed-lease parse commits nothing (two real connections); the worker
sweeps first. Each property was also broken on purpose and its test failed.

### F2: No command or UI to requeue a failed job (closed in stage 7)
Closed by the stage 7 commit. Requeue is the database function `requeue_failed_job()` (D-074):
controller only, idempotent, preserves `attempts`, refuses a job at its budget, and logs every
requeue in the append-only `job_requeue`. It is exposed on the Jobs page and tested directly
over HTTP and in the database.
