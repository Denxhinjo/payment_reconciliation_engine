# Deploying the reconciliation demo (Neon + Vercel + GitHub Actions)

Exact steps for a **new** free Neon project and a **new** Vercel project. Follow them in order.
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

Every night at 03:00 UTC a scheduled job **resets** `live` from `pristine` (Neon's "reset from
parent"), discarding whatever visitors did that day. Every page says so (decision D-085).

| Piece | Where | Connects as | To branch / connection |
|---|---|---|---|
| Migrations and demo seeding | your machine, by hand | `neondb_owner`, then `recon_worker_login` | `pristine`, direct |
| Web UI (`web/`) | Vercel | `recon_web_login` (member of `recon_web`) | `live`, **pooled** |
| Worker (`worker/`) | GitHub Actions, hourly | `recon_worker_login` (member of `recon_worker`) | `live`, direct |
| Nightly reset | GitHub Actions, 03:00 UTC | a project-scoped Neon API key | resets `live` from `pristine` |
| CI | GitHub Actions | none (its own Postgres service) | none |

The owner credential is used only on your machine, and is never stored in Vercel or GitHub.

**Verified on Neon** on 2026-10-07, on a throwaway project in this exact order, then deleted
(D-086):
- all ten migrations applied;
- the login roles were created with `IN ROLE` and passed the privilege check below;
- the first reconciliation produced the golden hash, and the replay was identical;
- `live` branched with roles and data intact;
- a project-scoped API key reset `live`;
- a signed-in session survived the reset;
- visitor data was gone afterwards.

## Order at a glance

1. Create the Neon project (Postgres 18) and rename its root branch to `pristine`.
2. Run the migrations on `pristine` (owner, from your machine).
3. Create the two login roles on `pristine` (owner, Neon SQL Editor).
4. Seed the synthetic staff on `pristine` (worker login).
5. Import the demo month on `pristine` (worker login).
6. First reconciliation and replay on `pristine`; check the hash.
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

## 1. Create the Neon project and name the root branch `pristine` (you)

- In the Neon console, create a new project. **Postgres version: 18.** The test suite and CI run
  on 18 (D-040).
- **Region:** close to the Vercel function region you will use in step 9 (e.g. Neon *AWS Europe
  Central 1 (Frankfurt)* with Vercel `fra1`).
- Keep the default database `neondb` and owner role `neondb_owner`.
- **Rename the root branch** (called `main` or `production`) to **`pristine`**: Branches →
  the branch → Rename.
- From **Connect**, with branch `pristine` selected, copy the **direct** connection string for
  `neondb_owner` (host without `-pooler`, ending in `?sslmode=require`).

**Check:** the project has one branch, `pristine`, on Postgres 18.

## 2. Run the migrations on `pristine` (you, owner)

```sh
cd worker
.venv/Scripts/python -m recon migrate --database-url "<neondb_owner DIRECT string for pristine>"
```

**Check:** the output lists exactly ten files, `0001_staff_and_files.sql` … `0010_job_lease.sql`.
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

## 4. Seed the synthetic staff on `pristine` (worker login)

```sh
.venv/Scripts/python -m recon seed-staff --database-url "<WORKER_URL_PRISTINE>"
```

**Check:** prints `synthetic staff: Demo Analyst 1, Demo Analyst 2, Demo Controller`.

## 5. Import the demo month on `pristine` (worker login)

From `worker/`, in this order (so the file ids are 1, 2, 3):

```sh
.venv/Scripts/python -m recon import --database-url "<WORKER_URL_PRISTINE>" --kind ledger     --file ../demo-data/2026-09/synthetic_ledger_2026-09.csv
.venv/Scripts/python -m recon import --database-url "<WORKER_URL_PRISTINE>" --kind settlement --file ../demo-data/2026-09/synthetic_orrery_settlement_2026-09.csv
.venv/Scripts/python -m recon import --database-url "<WORKER_URL_PRISTINE>" --kind bank       --file ../demo-data/2026-09/synthetic_bank_camt053_2026-09.xml
```

**Check:** `file #1 imported and parsed: 653 rows`, `file #2 … 612 rows`, `file #3 … 67 rows`.

## 6. First reconciliation and replay on `pristine` (worker login)

`GITHUB_SHA` records the commit the engine ran from (otherwise the run records none, F15):

```sh
GITHUB_SHA=$(git rev-parse HEAD) .venv/Scripts/python -m recon reconcile --database-url "<WORKER_URL_PRISTINE>" \
    --ledger-file 1 --settlement-file 2 --bank-file 3
.venv/Scripts/python -m recon replay --database-url "<WORKER_URL_PRISTINE>" --run 1
```

**Check:** the first command prints exactly

```
run #1 finished: 65 matches, 7 exceptions, result sha256 4962e8807d9a268581a698353b78c2d4ecce2551c8df340d2d7ea372285567c7
```

That hash is the golden result for engine 1.0.0 (`worker/tests/golden/results.json`). The second
command must end with `IDENTICAL: the run was reproduced byte for byte`. Any other hash means the
deployed code or data differs from what was tested: **stop**.

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
| Variable | `NEON_PROJECT_ID` | the project id (Settings → General in Neon, e.g. `abc-def-12345678`) |
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

From then on the worker runs hourly at minute 7 (F24), and the reset nightly at 03:00 UTC. Both
share one concurrency group, so a reset never interrupts a worker run.

Note: GitHub disables scheduled workflows in a repository with no activity for 60 days. If the
demo stops resetting or processing jobs after a quiet period, re-enable the workflows under
**Actions**.

## 9. Vercel: the web UI

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
   DATA** and *"This demo resets every night at 03:00 UTC; anything you change is discarded
   then."*, and the three synthetic accounts listed openly.
2. Sign in as **Demo Analyst 1**. **Runs** shows run #1 (finished, 65 matches, 7 exceptions)
   and run #2 (the replay from step 6, outcome `identical`).
3. **Run #1 → Open the exceptions queue**: seven open exceptions.
4. With no session, from a terminal:

   ```sh
   curl -s -o /dev/null -w "%{http_code}\n" https://<app>/runs                          # 307
   curl -s -o /dev/null -w "%{http_code}\n" -X POST https://<app>/api/jobs/1/requeue    # 401
   ```
5. **Replay as a job:** on run #1 press **Replay this run**. The page shows "Replay queued" and
   "Replay pending". There is no "Run now" button (F25): the replay appears after the next hourly
   worker run, or at once if you run the **worker** workflow by hand.
6. **Reset:** resolve one exception, then run **reset-demo** by hand. Afterwards the exception is
   open again and your resolution is gone, and you are still signed in.

If all six hold, the deployment matches what the tests and the Neon verification showed.

## Afterwards

- **Schema changes** are new numbered migrations. Apply them to **`pristine`** (step 2, owner),
  then run **reset-demo** by hand so `live` gets them. A migration applied to `live` alone is
  undone at the next reset. Never edit an applied migration; the runner refuses (D-035).
- **Rotating `SESSION_SECRET`** signs everyone out. That is harmless: sessions are only demo
  identities.
- **Do not run the test suite against Neon.** Tests create and drop databases and need a
  superuser (see `README.md`, "Running the tests").
- **What the reset does not guarantee** (D-085, D-086): visitors' changes stay visible until the
  next reset; at the moment of a reset, about one in-flight page load fails, and a reload works.
  See also `docs/proposal-public-writes.md`.
- **Known limits of this demo deployment:** `docs/known-fragilities.md`, especially F6 (anyone can
  sign in as any synthetic user, by design), F24 (compute budget) and F25 (no "Run now").
