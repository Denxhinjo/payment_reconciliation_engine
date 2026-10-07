-- 0010: job leases (closes fragility F19). See D-081.
--
-- A worker that claims a job also takes a lease on it (lease_until). A worker that dies leaves
-- the job 'running' with a lease that eventually lapses; the sweep at the start of every worker
-- invocation marks such jobs failed, with attempts preserved (they were counted at claim time).
-- They can then be requeued within their attempt budget, and a job that keeps dying stops at it.

ALTER TABLE job ADD COLUMN lease_until timestamptz;

-- Every running job has a lease; no other state needs one.
ALTER TABLE job ADD CONSTRAINT job_running_has_lease CHECK (status <> 'running' OR lease_until IS NOT NULL);

-- One definition of the lease length, used by every claim.
-- 15 minutes: the slowest normal job measured locally is a replay of the demo month (0.97 s);
-- the estimated worst case on Neon from GitHub Actions (about 100 sequential statements, each a
-- network round trip, plus a free-tier compute cold start) is about 30 s. 15 minutes is about 30x
-- that, and longer than the scheduled worker's 10-minute job timeout, so a worker is always
-- killed before its own lease can expire under it (D-081).
CREATE FUNCTION recon_job_lease() RETURNS interval
LANGUAGE sql IMMUTABLE AS $$ SELECT interval '15 minutes' $$;

-- The sweep: every job still running past its lease becomes failed. attempts is not touched.
-- Returns the ids it failed, so the worker can report them.
CREATE FUNCTION expire_job_leases() RETURNS SETOF bigint
LANGUAGE sql AS $$
  UPDATE job
     SET status = 'failed',
         finished_at = now(),
         error = 'lease expired: worker did not finish'
   WHERE status = 'running' AND lease_until < now()
  RETURNING id
$$;

-- Only the worker sweeps. (It runs as the caller, and recon_worker holds UPDATE on job.)
REVOKE ALL ON FUNCTION expire_job_leases() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION expire_job_leases() TO recon_worker;
