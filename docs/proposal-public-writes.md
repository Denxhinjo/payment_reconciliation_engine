# Proposal: what a public visitor may change (fragility F6)

**Status:** decided 2026-10-07: option (a), the nightly reset, is built (D-085, D-086). Option (b)
was not built; it can be added on top of (a) if abuse appears. This document is kept as the
reasoning behind the choice.
**Question:** the deployed demo is public. Any visitor can sign in as a demo staff member and
then upload files, request reconciliations and replays, and resolve or correct exceptions.
Should they be able to, and if so, how is the demo kept clean?

This document is written to be read by someone new to the domain, and to be defensible in front
of a client. Every technical term is explained the first time it appears.

---

## Why this is a real problem, in one paragraph

The system is deliberately **append-only**: once something is written (an uploaded file, a
resolution of an exception, a record of a run) the database refuses to change or delete it.
That is the point of the product. A finance team must be able to show, months later, exactly
what was recorded and by whom, and nobody can quietly edit history. On a public demo that same
property becomes a liability. Anything a visitor writes, including nonsense or offensive text in
a resolution note, is permanent and is shown to every later visitor. The application cannot
delete it, because the application is designed so that it cannot delete anything.

So either visitors are stopped from writing (option b), or the whole database is periodically
put back to a known clean state from *outside* the application (option a).

---

## Option (a): reset the database to a clean copy on a schedule

### What it does
Visitors can use everything, as now. Every night (or every few hours) the live database is
replaced, all at once, by a clean copy that contains only the prepared demo month. Whatever
visitors did since the last reset disappears.

### The mechanism
Neon, the database host, organises a database into **branches**. A branch is a full, independent
copy of the database that can be created in seconds, because it shares unchanged data with the
branch it came from (its **parent**). The first branch in a project is the **root branch**; it
has no parent.

1. **The clean copy.** The deployment steps in `docs/deploy.md` (migrations, login roles,
   synthetic staff, demo month, first reconciliation, first replay) are run on the root branch,
   which we would name `pristine`. Nobody connects to it afterwards.
2. **The live copy.** A child branch named `live` is created from `pristine`. The web app
   (Vercel) and the worker connect to `live` only.
3. **The reset.** A scheduled job, a GitHub Actions workflow on a **cron** schedule (a
   standard way of saying "run at these times", e.g. every night at 03:00 UTC), runs
   Neon's **reset from parent**: `neon branches reset live --parent`. Neon's documentation says
   this "instantly reset[s] all databases on a branch to the latest schema and data from its
   parent branch" and that "your connection details do not change". So the web app and worker
   carry on with the same connection string, and need no reconfiguration.

To run that command, the workflow needs a **Neon API key**: a secret that lets a program manage
the Neon project on your behalf. It would be stored as a GitHub Actions secret.

### What a visitor experiences
- Between resets, the demo behaves exactly as now. A visitor can resolve exceptions, upload
  files and request replays, and sees what earlier visitors did that day.
- At reset time, Neon's documentation says, "existing connections will be temporarily
  interrupted". A visitor using the demo at that moment may see one failed page load, then a
  fresh demo. Their sign-in survives: the synthetic staff accounts are identical in the clean
  copy, and the sign-in cookie only names which of them you are.
- After the reset: run #1 and its replay as originally prepared, seven open exceptions, no
  visitor uploads, no queued jobs.

### How it fits the free tier
- **Branches:** two (`pristine`, `live`), against a limit of 10 per project.
- **Storage:** the demo is a few megabytes, against 1 GB per project. Because branches share
  unchanged data, `live` adds only what visitors write since the last reset.
- **Compute:** `pristine` is never connected to, so its database engine stays paused. Neon pauses
  idle databases after 5 minutes ("scale to zero") and this cannot be switched off on the free
  plan. Whether the reset itself briefly wakes it is not stated in the pages consulted; it would
  be minutes a month at most.
- **Cost:** none. GitHub Actions minutes are free for public repositories.

### Why this approach
- It keeps the demo fully interactive. A prospective client can *do* the workflow (resolve,
  correct, replay), which is what the demo exists to show.
- It does not weaken the product's guarantee. The application still cannot delete anything. The
  clean-up is an infrastructure operation, the same kind of thing as restoring a backup, done
  outside the application by an operator-controlled job. That is an honest, explainable line:
  *"inside the system, history is append-only; the demo environment itself is rebuilt nightly
  from a clean image."*
- It heals itself: whatever happens, the damage lasts at most one reset interval.

### What it does NOT protect against
- **Abuse visible until the next reset.** Offensive text in a resolution note is shown to every
  visitor until the reset. A shorter interval narrows the window, but each reset interrupts
  connections briefly.
- **Filling the storage.** Uploads are capped at 4 MiB each, but nothing caps how many a visitor
  uploads. Enough of them in one interval could reach the 1 GB project limit. Writes would then
  fail, safely (the database refuses; nothing is half-written), until the next reset. Visitors
  in the meantime would find uploads and resolutions failing.
- **A visitor spoiling the demo for the next one**, e.g. by resolving every exception, so the
  next visitor finds an empty queue until the reset.
- **The API key.** It can manage the whole Neon project, including deleting branches, so its
  **blast radius** (how much damage a leaked secret could do) is larger than the database
  passwords'. It must stay a GitHub secret, with the narrowest key type Neon offers.
- **Evidence survives only until the next reset.** For a demo that is intended. A client may
  still ask "so someone *can* erase history?" The honest answer: yes, whoever controls the
  database infrastructure can, in any system; this is known fragility F7. The application, and
  anyone using it, cannot.
- **Changes in deploy order.** Schema changes and re-seeding must be applied to `pristine`, then
  `live` reset. Changing `live` directly would be undone at the next reset.

---

## Option (b): public read-only, with writes behind a shared demo password

### What it does
Anyone can look at everything without signing in. To change anything, a visitor must sign in,
and signing in requires a **shared demo password**: one password, the same for everyone allowed
to write, given out by you (e.g. to a prospective client during a call).

### The mechanism
- **Anonymous visitors see, read-only:** the runs list; each run's record, figures and replay
  status; the exceptions queue; each exception's evidence and full resolution history; the jobs
  list; the list of imported files. The forms and buttons are not shown.
- **The password unlocks:** uploading files, requesting reconciliations and replays, resolving
  and correcting exceptions, and (for the controller account) requeueing jobs. The sign-in page
  keeps the "sign in as" picker over the synthetic staff accounts and adds one password field.
- **Enforcement stays on the server, as now.** Every write already refuses a request without a
  valid session (401 "sign in required"). Only the sign-in step changes: a session is issued
  only after the password matches. The password lives in a Vercel environment variable. It is
  compared in **constant time** (a comparison that takes the same time whether the first
  character or the last is wrong, so timing reveals nothing), and wrong guesses are **rate
  limited** (each source may make only a few attempts in a time window), to slow **brute force**
  (trying passwords one after another).

### Why this approach
- Nothing a casual visitor does can change the demo. The demo always looks as you intend.
- No scheduled job and no API key to hold.
- The read-only view still shows the whole product: the matches, the exceptions with evidence,
  the timing deadlines, the replay proof, the append-only history.

### What it does NOT protect against
- **A shared password leaks.** People forward links and passwords. Once it is out, anyone can
  write, and because the system is append-only, **what they write is permanent**. There is no
  way to remove it without resetting the database, which is option (a) anyway. Its failure mode
  is therefore worse than (a)'s: unbounded in time instead of bounded by the reset interval.
- **Knowing who did what.** Everyone with the password acts as one of the same three synthetic
  staff accounts. The audit trail records "Demo Analyst 1", not which real person it was.
- **Brute force on Vercel.** Vercel runs the app as many short-lived **serverless** instances
  (copies started on demand) that share no memory. A rate limit kept in memory, as in the KYC
  demo, would apply per instance, not globally. A real limit would need a small database table.
- **Casual visitors can't try the workflow.** The most persuasive part of the demo (resolving
  an exception and seeing the correction chain) becomes something they only read about.

---

## Recommendation: option (a), a nightly reset

Option (a) keeps the demo interactive, which is what it is for, and its worst case is bounded:
whatever goes wrong is undone within one night. Option (b)'s worst case, a leaked password, is
unbounded, and its only cure is the reset mechanism of option (a) anyway. Option (a) also keeps
the product's story intact and easy to explain: the application never deletes, while the demo
*environment* is rebuilt from a clean image, as any staging environment would be.

**What the recommendation accepts:** abuse may be visible for up to one reset interval, and a
determined visitor could fill the storage or empty the queue until then. If either happens in
practice, the next step is to add (b)'s password on top of (a), keeping the nightly reset as the
safety net. That combination costs both mechanisms' complexity, so start with (a) alone.

**If chosen, building it would mean** (not done; for planning):
1. A workflow `reset-demo.yml`: nightly cron and manual trigger, inert until enabled like
   `worker.yml`, running `neon branches reset live --parent` with a `NEON_API_KEY` secret.
2. `docs/deploy.md` changed so steps 2–6 run on `pristine`, followed by creating `live`, with
   Vercel and the worker pointed at `live`.
3. A visible line on every page: "This demo resets every night at 03:00 UTC".
4. Verification on a throwaway Neon project before relying on it: that `reset --parent` restores
   the login roles and the seeded data, that a session survives a reset, and how long the
   connection interruption lasts. The Neon documentation consulted does not state the last two.

## Sources consulted (2026-10-07)

- Neon documentation, "Reset from parent": https://neon.com/docs/guides/reset-from-parent
  (what reset does; connection details unchanged; connections briefly interrupted; root branches
  and branches with children cannot be reset; CLI `neon branches reset <id|name> --parent`).
- Neon documentation, "Plans": https://neon.com/docs/introduction/plans (Free plan: 10 branches
  per project, 1 GB storage per project, 100 CU-hours per project per month, scale to zero after
  5 minutes which cannot be disabled, 6-hour restore window).
- Neon CLI `neon@7.0.6`, `neon branches reset --help` (options `--parent`,
  `--preserve-under-name`).
