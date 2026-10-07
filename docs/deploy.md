# Deploying the reconciliation demo (Neon + Vercel + GitHub Actions)

Exact steps for a **new** free Neon project and a **new** Vercel project. Nothing here has been
run against your accounts; follow the steps in order. Each step ends with a check, and **if a
check fails, stop**: do not continue past a step that did not do what it says.

Everything deployed is synthetic demo data. No step needs real data, and none should ever be
given any.

## What runs where

| Piece | Where | Connects to Neon as | Connection string |
|---|---|---|---|
| Migrations (schema) | your machine, by hand | `neondb_owner` (the owner) | direct |
| Web UI (`web/`) | Vercel | `recon_web_login` (member of `recon_web`) | **pooled** |
| Worker (`worker/`) | GitHub Actions, every 15 min | `recon_worker_login` (member of `recon_worker`) | direct |
| CI | GitHub Actions | none (its own Postgres service) | none |

The owner credential is used only on your machine and is never stored in Vercel or GitHub. The
web role cannot UPDATE or DELETE anything; the worker role cannot touch resolutions or
migrations (D-019, D-039, D-074).

## Order at a glance

1. Create the Neon project (Postgres 18).
2. Run the migrations (owner, from your machine).
3. Create the two login roles (owner, Neon SQL Editor).
4. Seed the synthetic staff (worker login).
5. Import the demo month (worker login).
6. Run the first reconciliation and check its hash (worker login).
7. GitHub Actions: secret and variable for the scheduled worker.
8. Vercel: create the project, set two environment variables, deploy.
9. Smoke-test the deployment.

## Before you start

On your machine, from a clone of the repository at the commit you are deploying:

```sh
cd worker
python -m venv .venv
.venv/Scripts/python -m pip install "psycopg[binary]==3.3.6" "lxml==6.1.3"   # Windows paths;
# on macOS/Linux use .venv/bin/python
```

You will need three random secrets: two database passwords and the session secret. Generate
each with:

```sh
.venv/Scripts/python -c "import secrets; print(secrets.token_urlsafe(36))"
```

Keep them in a password manager. They are not demo values.

---

## 1. Create the Neon project (you)

- In the Neon console, create a new project. **Postgres version: 18.** The test suite and CI run
  on 18 (D-040).
- **Region:** pick one close to the Vercel function region you will use in step 8 (e.g. Neon
  *AWS Europe Central 1 (Frankfurt)* with Vercel `fra1`). Every page load makes several queries,
  so distance between the two adds up.
- Keep the default database `neondb` and owner role `neondb_owner`.
- From **Connect**, copy two connection strings for `neondb_owner`:
  - the **direct** one (host without `-pooler`), and
  - the **pooled** one (host contains `-pooler`).

  Both end in `?sslmode=require`. Only the direct one is used with the owner (step 2). The
  pooled *host* is reused for the web login in step 3.

**Check:** the project shows Postgres 18 and one branch, `main` (or `production`).

## 2. Run the migrations (you, owner, from your machine)

```sh
cd worker
.venv/Scripts/python -m recon migrate --database-url "<neondb_owner DIRECT connection string>"
```

**Check:** the output lists exactly ten files, in order:

```
applied: 0001_staff_and_files.sql, 0002_input_rows.sql, 0003_runs.sql, 0004_resolutions.sql,
0005_jobs.sql, 0006_roles.sql, 0007_exception_queue.sql, 0008_run_overview.sql, 0009_web.sql,
0010_job_lease.sql
```

Running it a second time must print `database is up to date`. If it prints `migration refused`,
stop: the runner refuses a database whose history does not match the files (D-035).

*Verified beforehand:* migrations 0001–0006, including creating the `recon_web` and
`recon_worker` roles as `neondb_owner`, were applied once to a throwaway Neon branch on Postgres
18.6 (decisions, "Stage 1 follow-up"). Migrations 0007–0010 have run only on local and CI
Postgres 18.

## 3. Create the two login roles (you, owner, Neon SQL Editor)

In the Neon console, open **SQL Editor** on `neondb` (it runs as `neondb_owner`). Run, with your
two generated passwords:

```sql
CREATE ROLE recon_web_login    LOGIN PASSWORD '<web password>'    IN ROLE recon_web;
CREATE ROLE recon_worker_login LOGIN PASSWORD '<worker password>' IN ROLE recon_worker;
```

Then check what each can do. All seven rows must match `expected`:

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

**Check:** `actual` equals `expected` on every row. *Not yet verified on Neon:* granting
membership with `IN ROLE` as `neondb_owner`. On Postgres 16+ the creator of a role holds ADMIN
on it, which is what the grant needs, and that held on the throwaway branch. If Neon refuses
either statement, stop and report the exact error. Do not work around it, e.g. by granting
table privileges to the login roles directly.

Now build the two connection strings by putting each login's name and password into Neon's
strings (keep `?sslmode=require`):

- **WEB_URL**: the **pooled** host, user `recon_web_login`.
- **WORKER_URL**: the **direct** host, user `recon_worker_login`.

The web app uses the pooled host because Vercel opens many short-lived connections. The worker
uses the direct host because it holds row locks (`FOR UPDATE SKIP LOCKED`) across a transaction.

## 4. Seed the synthetic staff (worker login)

```sh
.venv/Scripts/python -m recon seed-staff --database-url "<WORKER_URL>"
```

**Check:** prints `synthetic staff: Demo Analyst 1, Demo Analyst 2, Demo Controller`.

## 5. Import the demo month (worker login)

From `worker/`, in this order (so the file ids are 1, 2, 3):

```sh
.venv/Scripts/python -m recon import --database-url "<WORKER_URL>" --kind ledger     --file ../demo-data/2026-09/synthetic_ledger_2026-09.csv
.venv/Scripts/python -m recon import --database-url "<WORKER_URL>" --kind settlement --file ../demo-data/2026-09/synthetic_orrery_settlement_2026-09.csv
.venv/Scripts/python -m recon import --database-url "<WORKER_URL>" --kind bank       --file ../demo-data/2026-09/synthetic_bank_camt053_2026-09.xml
```

**Check:** the three lines read `file #1 imported and parsed: 653 rows`, `file #2 … 612 rows`
and `file #3 … 67 rows`. Re-running any of them must print `already imported as file #N …;
nothing done`.

## 6. First reconciliation, and check its hash (worker login)

To record the commit the engine ran from on this run, set `GITHUB_SHA` to it first (otherwise
the run records no commit, which is F15):

```sh
GITHUB_SHA=$(git rev-parse HEAD) .venv/Scripts/python -m recon reconcile --database-url "<WORKER_URL>" \
    --ledger-file 1 --settlement-file 2 --bank-file 3
```

**Check:** exactly

```
run #1 finished: 65 matches, 7 exceptions, result sha256 4962e8807d9a268581a698353b78c2d4ecce2551c8df340d2d7ea372285567c7
```

That hash is the golden result for engine 1.0.0 (`worker/tests/golden/results.json`). Any other
hash means the deployed code or data differs from what was tested: **stop**.

Then prove replay on the deployed database:

```sh
.venv/Scripts/python -m recon replay --database-url "<WORKER_URL>" --run 1
```

**Check:** ends with `IDENTICAL: the run was reproduced byte for byte`.

## 7. GitHub Actions: the scheduled worker

In the repository's **Settings → Secrets and variables → Actions**:

- **Secret** `RECON_WORKER_DATABASE_URL` = WORKER_URL.
- **Variable** `RECON_WORKER_ENABLED` = `true`.

Until that variable is `true`, `.github/workflows/worker.yml` skips every scheduled run. That is
why pushing the workflow deployed nothing. CI (`ci.yml`) needs no secrets.

Then **Actions → worker → Run workflow** once.

**Check:** the run succeeds and its log ends with
`0 parse job(s), 0 reconcile job(s) and 0 replay job(s) processed`. From then on it runs every 15
minutes. Each invocation first fails any job whose worker died (lease expired, attempts kept),
then processes the queue (D-081). Its 10-minute timeout is below the 15-minute lease on purpose.

Note: GitHub disables scheduled workflows in a repository with no activity for 60 days. If the
demo stops processing jobs after a quiet period, re-enable the workflow under **Actions**.

## 8. Vercel: the web UI

- **Add New → Project →** import `Denxhinjo/payment_reconciliation_engine`.
- **Root Directory: `web`.** The framework is detected as Next.js; leave build and output
  settings at their defaults.
- **Node.js version:** 22.x (Project Settings → General), matching CI.
- **Function region:** the one next to your Neon region (step 1).
- **Environment variables** (Production):

  | Name | Value |
  |---|---|
  | `DATABASE_URL` | WEB_URL (pooled host, `recon_web_login`) |
  | `SESSION_SECRET` | the third generated secret (at least 32 characters) |

  Nothing else. In particular, never the owner's or the worker's credentials.
- **Deploy.**

**Check:** the build log shows the same route table as CI: every route dynamic (`ƒ`) except
`/_not-found`.

## 9. Smoke test the deployment

Replace `<app>` with your Vercel URL.

1. Open `https://<app>/`: you land on the sign-in page with the yellow
   **DEMO — SYNTHETIC DATA** banner and the three synthetic accounts listed openly.
2. Sign in as **Demo Analyst 1**. **Runs** shows run #1 (finished, 65 matches, 7 exceptions)
   and run #2 (the replay from step 6, outcome `identical`).
3. **Run #1 → Open the exceptions queue**: seven open exceptions.
4. Authorisation, from a terminal, with no session:

   ```sh
   curl -s -o /dev/null -w "%{http_code}\n" https://<app>/runs                          # 307
   curl -s -o /dev/null -w "%{http_code}\n" -X POST https://<app>/api/jobs/1/requeue    # 401
   ```
5. **Replay as a job:** on run #1, press **Replay this run**. The page shows "Replay queued" and
   "Replay pending". Within 15 minutes (the next scheduled worker run, or run the workflow by
   hand) a new replay run appears with outcome `identical`.

If all five hold, the deployment matches what the tests verify.

## Afterwards

- **Rotating `SESSION_SECRET`** signs everyone out. That is harmless here, since sessions are
  only demo identities.
- **Do not run the test suite against Neon.** Tests create and drop databases and need a
  superuser (see `README.md`, "Running the tests").
- **Schema changes** are new numbered migrations, applied by step 2 again. Never edit an applied
  migration; the runner refuses (D-035).
- **Known limits of this demo deployment:** see `docs/known-fragilities.md`. In particular F6
  (anyone can sign in as any synthetic user, by design) and F15 (runs created by hand record a
  commit only if `GITHUB_SHA` is set, as in step 6).
