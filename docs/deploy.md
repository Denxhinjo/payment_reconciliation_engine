# Deploying the reconciliation demo (Neon + Vercel + GitHub Actions)

Exact steps for the existing, empty free Neon project **`payment_reconciliation_engine`** and a
**new** Vercel project. Follow them in order.
Each step ends with a check, and **if a check fails, stop**: do not continue past a step that did
not do what it says.

Everything deployed is synthetic demo data. No step needs real data, and none should ever be
given any.

## The shape of the deployment, in plain words

The demo is public, and the system is **append-only** (the database refuses to edit or delete
anything once written), so visitors' changes would otherwise be permanent. The deployment
therefore keeps **two copies** of the database, as Neon **branches** (cheap, independent copies,
where a child branch starts as an exact copy of its **parent**):

- **`pristine`**: the root branch. It is prepared once with the demo month, and nobody connects to
  it afterwards.
- **`live`**: a child of `pristine`. The website and the worker use only this one.

Once a day, scheduled for 03:00 UTC, a job **resets** `live` from `pristine` (Neon's "reset from
parent"), discarding whatever visitors did that day. Every page says so (decision D-085). GitHub
does not guarantee start times, so it usually runs around then, sometimes hours later (D-093).

| Piece | Where | Connects as | To branch / connection |
|---|---|---|---|
| Migrations and demo seeding | your machine, by hand | `neondb_owner`, then `recon_worker_login` | `pristine`, direct |
| Web UI (`web/`) | Vercel | `recon_web_login` (member of `recon_web`) | `live`, **pooled** |
| Worker (`worker/`) | GitHub Actions, hourly | `recon_worker_login` (member of `recon_worker`) | `live`, direct |
| Daily reset | GitHub Actions, scheduled 03:00 UTC | a project-scoped Neon API key | resets `live` from `pristine` |
| CI | GitHub Actions | none (its own Postgres service) | none |

The owner credential is used only on your machine, and is never stored in Vercel or GitHub.

**Verified on Neon** on 2026-10-07, on a throwaway project in this exact order, then deleted
(D-086). Migration 0011 (queue status, D-088) was written afterwards and has run only on local
and CI Postgres 18; step 2 is its first run on Neon.
- all ten migrations of the time applied;
- the login roles were created with `IN ROLE` and passed the privilege check below;
- the first reconciliation produced the golden hash, and the replay was identical;
- `live` branched with roles and data intact;
- a project-scoped API key reset `live`;
- a signed-in session survived the reset;
- visitor data was gone afterwards.

## Order at a glance

1. Use the existing Neon project `payment_reconciliation_engine`; check it is empty; rename its
   branch to `pristine`.
2. Run the migrations on `pristine` (owner, from your machine).
3. Create the two login roles on `pristine` (owner, Neon SQL Editor).
4. Seed the demo on `pristine` through the job path (worker login); check the hash.
5. Check what the seed left.
6. Leave `pristine` alone from now on.
7. Create the branch `live` from `pristine`; build the two connection strings for `live`.
8. GitHub Actions: secrets and variables for the worker and the nightly reset; run each once.
9. Vercel: create the project, set two environment variables, deploy.
10. Smoke-test the deployment.

## Before you start

On your machine, from a clone of the repository at the commit you are deploying:

```sh
cd worker
python -m venv .venv
.venv/Scripts/python -m pip install "psycopg[binary]==3.3.6" "lxml==6.1.3"   # Windows paths;
# on macOS/Linux use .venv/bin/python
```

You will need three random secrets: two database passwords and the session secret. Generate each
with:

```sh
.venv/Scripts/python -c "import secrets; print(secrets.token_urlsafe(36))"
```

Keep them in a password manager. They are not demo values.

---

## 1. Use the existing project `payment_reconciliation_engine` (you)

The project already exists in your Neon organisation:
- id `weathered-star-14803706`;
- region AWS Europe Central 1 (Frankfurt);
- **Postgres 18** (the version the tests and CI run on, D-040);
- one branch, `production`.

A read-only check on 2026-10-08 found its database empty: no tables, and no roles besides
Neon's own. The roles from the 2026-10-02 role test lived only on a temporary branch that was
deleted.

- **Rename its branch** `production` to **`pristine`**: Neon console → the project → Branches →
  `production` → Rename.
- **Confirm it is still empty**: SQL Editor, branch `pristine`, database `neondb`:

  ```sql
  SELECT count(*) AS tables FROM pg_tables WHERE schemaname = 'public';
  ```

  **Check:** `tables` is `0`. If it is not, stop: something was created since 2026-10-08.
- From **Connect**, with branch `pristine` selected, copy the **direct** connection string for
  `neondb_owner` (host without `-pooler`, ending in `?sslmode=require`).
- Note the project id `weathered-star-14803706`; step 8 needs it.
- **Vercel region** (step 9): Frankfurt, `fra1`, next to the database.

**Check:** the project has one branch, `pristine`, on Postgres 18, with no tables.

## 2. Run the migrations on `pristine` (you, owner)

```sh
cd worker
.venv/Scripts/python -m recon migrate --database-url "<neondb_owner DIRECT string for pristine>"
```

**Check:** the output lists exactly twelve files, `0001_staff_and_files.sql` …
`0012_job_requester.sql`.
Running it again must print `database is up to date`. If it prints `migration refused`, stop
(D-035).

## 3. Create the two login roles on `pristine` (you, owner, Neon SQL Editor)

Open **SQL Editor**, branch **`pristine`**, database `neondb`. Run, with your two generated
passwords:

```sql
CREATE ROLE recon_web_login    LOGIN PASSWORD '<web password>'    IN ROLE recon_web;
CREATE ROLE recon_worker_login LOGIN PASSWORD '<worker password>' IN ROLE recon_worker;
```

Then check what each can do. All seven rows must show `actual` equal to `expected`:

```sql
SELECT r, t, p, has_table_privilege(r, t, p) AS actual, e AS expected
  FROM (VALUES
    ('recon_web_login',    'resolution',         'INSERT', true),
    ('recon_web_login',    'resolution',         'UPDATE', false),
    ('recon_web_login',    'job',                'UPDATE', false),
    ('recon_worker_login', 'reconciliation_run', 'UPDATE', true),
    ('recon_worker_login', 'resolution',         'UPDATE', false),
    ('recon_worker_login', 'ledger_entry',       'DELETE', false),
    ('recon_web_login',    'schema_migration',   'SELECT', false)
  ) AS v(r, t, p, e);
```

Roles are created on `pristine` **before** `live` exists, so `live` inherits them, and every
reset restores them.

Build **WORKER_URL_PRISTINE**: the `pristine` direct string, with user `recon_worker_login` and
its password (keep `?sslmode=require`).

## 4. Seed the demo on `pristine` through the job path (worker login)

One command, from `worker/`. `GITHUB_SHA` records the commit the engine ran from (otherwise the
runs record none, F15):

```sh
GITHUB_SHA=$(git rev-parse HEAD) .venv/Scripts/python -m recon seed-demo --database-url "<WORKER_URL_PRISTINE>"
```

It creates the synthetic staff, then does what a user and the worker would do, as the system
actor "Deployment seed" (D-091, D-092): it uploads the three demo files and runs their parse
jobs, queues and runs the reconciliation (run #1), and queues and runs its replay (run #2). It is
one transaction: if anything fails, nothing is kept and it prints `SEED FAILED, nothing kept`.

**Check:** the output contains

```
run #1: 65 matches, 7 exceptions
  golden result sha256 4962e8807d9a268581a698353b78c2d4ecce2551c8df340d2d7ea372285567c7
  run    result sha256 4962e8807d9a268581a698353b78c2d4ecce2551c8df340d2d7ea372285567c7
run #2 replays run #1: IDENTICAL (4962e8807d9a268581a698353b78c2d4ecce2551c8df340d2d7ea372285567c7)
```

and the last line is `READY`. The golden hash is the result for engine 1.0.0
(`worker/tests/golden/results.json`). The command itself refuses a different hash, and you
should too: **stop**, never edit the golden value.

## 5. Check what the seed left (worker login)

```sh
.venv/Scripts/python -m recon queue --database-url "<WORKER_URL_PRISTINE>" --run 1 --status open
```

**Check:** ends with `7 exception(s), 7 open`.

## 6. Leave `pristine` alone

`pristine` is now the clean image. **Do not connect anything to it again**, except to apply a
future migration (see "Afterwards").

## 7. Create `live`, and the two connection strings for it

- Neon console → Branches → **Create branch**: name **`live`**, parent **`pristine`**, from the
  latest data.
- From **Connect**, with branch **`live`** selected, copy both the **direct** and the **pooled**
  connection strings, then put the login roles into them (keep `?sslmode=require`):
  - **WORKER_URL**: `live` **direct** host, user `recon_worker_login`.
  - **WEB_URL**: `live` **pooled** host (contains `-pooler`), user `recon_web_login`.

  The web app uses the pooled host because Vercel opens many short-lived connections. The worker
  uses the direct host because it holds row locks (`FOR UPDATE SKIP LOCKED`) inside a transaction.

**Check:**

```sh
.venv/Scripts/python -m recon queue --database-url "<WORKER_URL>" --run 1 --status open
```

ends with `7 exception(s), 7 open`.

## 8. GitHub Actions: the worker and the nightly reset

**Create a project-scoped Neon API key.** Neon console → your organisation's settings → API keys
→ create a key **scoped to this project only**. Neon documents that such a key "cannot delete the
project". We verified that it can reset a branch (D-086). Do not use a personal or organisation
key, which can do more.

In the repository's **Settings → Secrets and variables → Actions**:

| Kind | Name | Value |
|---|---|---|
| Secret | `RECON_WORKER_DATABASE_URL` | WORKER_URL (`live`, direct, `recon_worker_login`) |
| Secret | `NEON_API_KEY` | the project-scoped key |
| Variable | `NEON_PROJECT_ID` | `weathered-star-14803706` |
| Variable | `RECON_WORKER_ENABLED` | `true` |
| Variable | `RECON_RESET_ENABLED` | `true` |

Until those two variables are `true`, `worker.yml` and `reset-demo.yml` skip every run. That is
why pushing them deployed nothing. CI (`ci.yml`) needs no secrets.

Then, under **Actions**, run each workflow once by hand:

1. **worker → Run workflow.** **Check:** succeeds, and the log ends with
   `0 parse job(s), 0 reconcile job(s) and 0 replay job(s) processed`.
2. **reset-demo → Run workflow.** **Check:** succeeds, and the log ends with
   `live is the pristine demo`. The workflow itself fails if `live` is not exactly the prepared
   demo after the reset.

From then on the worker runs hourly at minute 7 (F24), and the reset once a day, scheduled for
03:00 UTC (D-093). Both
share one concurrency group, so a reset never interrupts a worker run.

**Important:** GitHub pauses these two scheduled workflows after 60 days without repository
activity. See "If the demo stops resetting or processing jobs" below.

## 9. Vercel: the web UI

- **Deployed 2026-10-08 (D-090)** as `payment-reconciliation-demo`, at
  https://payment-reconciliation-demo.vercel.app. `web/vercel.json` declares the framework
  (`nextjs`) and region (`fra1`), and `web/package.json` the Node version (`22.x`). These come
  from the repository, whatever the dashboard says. Without the framework declared, a
  project created outside the import flow is built as a static site, and the deployment
  fails looking for `public/`.
- **Add New → Project →** import `Denxhinjo/payment_reconciliation_engine`.
- **Root Directory: `web`.** The framework is detected as Next.js; leave build settings as
  default.
- **Node.js version:** 22.x (Project Settings → General).
- **Function region:** the one next to your Neon region.
- **Environment variables** (Production), nothing else:

  | Name | Value |
  |---|---|
  | `DATABASE_URL` | WEB_URL (`live`, pooled, `recon_web_login`) |
  | `SESSION_SECRET` | the third generated secret (at least 32 characters) |

- **Deploy.**

**Check:** the build log shows every route as dynamic (`ƒ`) except `/_not-found`.

## 10. Smoke test

Replace `<app>` with your Vercel URL.

1. `https://<app>/` shows the sign-in page, with the yellow banner reading **DEMO — SYNTHETIC
   DATA** and *"This demo resets once a day, usually around 03:00 UTC; anything you change is
   discarded then."*, and the three synthetic accounts listed openly. "Deployment seed" is
   **not** listed: it is the system actor and cannot sign in.
2. Sign in as **Demo Analyst 1**. **Runs** shows run #1 (finished, 65 matches, 7 exceptions)
   and run #2 (the replay from step 4, outcome `identical`), both requested by
   "Deployment seed" with the label **system actor**.
3. **Run #1 → Open the exceptions queue**: seven open exceptions.
4. With no session, from a terminal:

   ```sh
   curl -s -o /dev/null -w "%{http_code}\n" https://<app>/runs                          # 307
   curl -s -o /dev/null -w "%{http_code}\n" -X POST https://<app>/api/jobs/1/requeue    # 401
   ```
5. **The replay proof is there at once:** run #1's page lists run #2 as a replay with outcome
   **identical**, and both hashes equal `4962e880…67c7`. Nobody has to wait for anything. The
   nightly reset checks this every night and fails if it is missing.
6. **Replay as a job:** on run #1 press **Replay this run**. The page says *"Replay queued: the
   worker runs at 7 minutes past every hour."* and lists *"Replay pending (job #N). Queued: the
   worker runs at 7 minutes past every hour."* There is no "Run now" button (F25): the replay
   appears after the next hourly worker run, or at once if you run the **worker** workflow by
   hand. If the page instead says *"Queued for over 70 minutes: the worker should have run by
   now…"*, the worker is not running: see the section below.
7. **Reset:** resolve one exception, then run **reset-demo** by hand. Afterwards the exception is
   open again and your resolution is gone, and you are still signed in.

If all seven hold, the deployment matches what the tests and the Neon verification showed.


## If the demo stops resetting or processing jobs: GitHub's 60-day pause

GitHub's documentation (*Disabling and enabling a workflow*) states: *"In a public repository,
scheduled workflows are automatically disabled when no repository activity has occurred in 60
days."* Both `worker` and `reset-demo` are scheduled workflows, so after two quiet months:

- **Symptoms:** queued jobs never run, and the site says *"Queued for over 70 minutes: the worker
  should have run by now…"* (D-088). Visitors' changes are no longer discarded at 03:00 UTC.
- **To re-enable, in the browser:** the repository → **Actions** tab → choose **worker** in the
  left sidebar → **Enable workflow**. Then do the same for **reset-demo**.
- **Or with the GitHub CLI:**

  ```sh
  gh workflow enable worker.yml
  gh workflow enable reset-demo.yml
  ```

- **Then** run each by hand once (step 8) to catch up, and check the reset ends with
  `live is the pristine demo`.

GitHub's page does not define exactly what counts as "repository activity", so this guide does not
promise that any particular action prevents the pause. The site's overdue message is the signal
to look.

## Afterwards

- **Schema changes** are new numbered migrations. Apply them to **`pristine`** (step 2, owner),
  then run **reset-demo** by hand so `live` gets them. A migration applied to `live` alone is
  undone at the next reset. Never edit an applied migration; the runner refuses (D-035).
- **Rotating `SESSION_SECRET`** signs everyone out. That is harmless: sessions are only demo
  identities.
- **Rotating a database password:** change it on **`pristine`** (Neon SQL Editor, branch
  `pristine`: `ALTER ROLE recon_web_login PASSWORD '<new>';`), then run **reset-demo** so `live`
  gets it. A change made on `live` alone is undone at the next reset. Then update the matching
  secret: Vercel `DATABASE_URL` for the web login (and redeploy), GitHub
  `RECON_WORKER_DATABASE_URL` for the worker login. The passwords from the first deployment exist
  only in those two places; nobody holds a copy.
- **Rotating the Neon API key:** create a new project-scoped key, set it as the GitHub secret
  `NEON_API_KEY`, run **reset-demo** once to check it, then revoke the old key in Neon.
- **Do not run the test suite against Neon.** Tests create and drop databases and need a
  superuser (see `README.md`, "Running the tests").
- **What the reset does not guarantee** (D-085, D-086): visitors' changes stay visible until the
  next reset; at the moment of a reset, about one in-flight page load fails, and a reload works.
  See also `docs/proposal-public-writes.md`.
- **Known limits of this demo deployment:** `docs/known-fragilities.md`, especially F6 (anyone can
  sign in as any synthetic user, by design), F24 (compute budget) and F25 (no "Run now").
