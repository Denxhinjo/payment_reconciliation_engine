-- 0009: what the web UI (stage 7) needs from the data layer. See D-073 to D-076.
--
-- The web app connects as a member of recon_web, which can SELECT and INSERT but never UPDATE.
-- Everything the UI is allowed to *change* beyond inserts goes through a function defined here,
-- so the rule is enforced by the database whatever the UI does.
--
-- SQLSTATEs raised here:
--   RC005  requeue attempted by a staff user who is not a controller

-- --- Staff roles (D-073) ---------------------------------------------------------------------
-- 'analyst' works the queue; 'controller' may also requeue failed jobs. Existing rows default to
-- analyst. Adding a column is DDL, not an UPDATE, so the append-only trigger is not involved.
ALTER TABLE staff_user
  ADD COLUMN role text NOT NULL DEFAULT 'analyst' CHECK (role IN ('analyst', 'controller'));

-- --- Attempt budget: one definition for the worker and for requeue (D-074) -------------------
-- attempts is incremented when a worker CLAIMS a job, so a job that kills its worker still
-- counts as attempted. A job at the budget is never claimed again (poison-pill protection), and
-- requeue never resets the counter.
CREATE FUNCTION recon_max_job_attempts() RETURNS integer
LANGUAGE sql IMMUTABLE AS $$ SELECT 3 $$;

-- --- Requeue log: append-only record of every requeue (D-074) --------------------------------
CREATE TABLE job_requeue (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  job_id              bigint NOT NULL REFERENCES job (id),
  requeued_by         bigint NOT NULL REFERENCES staff_user (id),
  requeued_at         timestamptz NOT NULL DEFAULT now(),
  attempts_at_requeue integer NOT NULL CHECK (attempts_at_requeue >= 0),
  previous_error      text NOT NULL
);
CREATE TRIGGER job_requeue_append_only BEFORE UPDATE OR DELETE ON job_requeue
  FOR EACH ROW EXECUTE FUNCTION reject_modification();
CREATE TRIGGER job_requeue_no_truncate BEFORE TRUNCATE ON job_requeue
  FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();

-- --- Requeue: the only way to put a failed job back in the queue (D-074) ---------------------
-- Idempotent: a second call finds the job already queued and changes nothing, so a double click
-- or a re-submitted form produces one requeue. attempts is preserved. The error moves to the
-- requeue log, so the job's current error never describes an earlier attempt.
-- Returns: 'requeued' | 'already_queued' | 'not_failed' | 'attempts_exhausted' | 'not_found'.
CREATE FUNCTION requeue_failed_job(p_job_id bigint, p_staff_id bigint) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_role text;
  v_job  job%ROWTYPE;
BEGIN
  SELECT role INTO v_role FROM staff_user WHERE id = p_staff_id;
  IF v_role IS DISTINCT FROM 'controller' THEN
    RAISE EXCEPTION 'only a controller may requeue jobs (staff % is %)', p_staff_id, coalesce(v_role, 'unknown')
      USING ERRCODE = 'RC005';
  END IF;
  -- Row lock: two simultaneous requeues of the same job serialise here; the second sees 'queued'.
  SELECT * INTO v_job FROM job WHERE id = p_job_id FOR UPDATE;
  IF NOT FOUND THEN
    RETURN 'not_found';
  END IF;
  IF v_job.status IN ('queued', 'running') THEN
    RETURN 'already_queued';
  END IF;
  IF v_job.status <> 'failed' THEN
    RETURN 'not_failed';
  END IF;
  IF v_job.attempts >= recon_max_job_attempts() THEN
    RETURN 'attempts_exhausted';
  END IF;
  INSERT INTO job_requeue (job_id, requeued_by, attempts_at_requeue, previous_error)
  VALUES (v_job.id, p_staff_id, v_job.attempts, v_job.error);
  UPDATE job SET status = 'queued', error = NULL, started_at = NULL, finished_at = NULL
   WHERE id = v_job.id;          -- attempts deliberately untouched
  RETURN 'requeued';
END;
$$;

REVOKE ALL ON FUNCTION requeue_failed_job(bigint, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION requeue_failed_job(bigint, bigint) TO recon_web;
GRANT SELECT ON job_requeue TO recon_web, recon_worker;

-- --- Double submissions of the same request create one job (D-075) ---------------------------
-- While a replay of run N is queued or running, a second replay request is a no-op; the same for
-- a reconcile of the same three files. Once the job is done or failed it leaves the index, so a
-- later deliberate request is allowed.
CREATE UNIQUE INDEX job_one_active_replay_per_run ON job (replay_of_run_id)
  WHERE kind = 'replay' AND status IN ('queued', 'running');
CREATE UNIQUE INDEX job_one_active_reconcile_per_inputs
  ON job (ledger_file_id, settlement_file_id, bank_file_id)
  WHERE kind = 'reconcile' AND status IN ('queued', 'running');

-- --- run_overview: every figure the UI shows comes from here (D-076) -------------------------
-- New columns are appended (CREATE OR REPLACE VIEW may only add columns at the end).
-- replay_outcome distinguishes what replay_identical flattens: a replay that RAN AND DIFFERED
-- ('different') is a different event from a replay that FAILED TO RUN ('failed').
CREATE OR REPLACE VIEW run_overview AS
SELECT r.id                               AS run_id,
       r.engine_version,
       r.engine_git_sha,
       r.status,
       r.error,
       r.started_at,
       r.finished_at,
       encode(r.result_sha256, 'hex')     AS result_sha256,
       r.ledger_file_id,
       encode(lf.sha256, 'hex')           AS ledger_sha256,
       r.settlement_file_id,
       encode(sf.sha256, 'hex')           AS settlement_sha256,
       r.bank_file_id,
       encode(bf.sha256, 'hex')           AS bank_sha256,
       r.replay_of_run_id,
       CASE WHEN r.replay_of_run_id IS NULL THEN NULL
            ELSE r.status = 'finished' AND r.result_sha256 IS NOT DISTINCT FROM o.result_sha256
       END                                AS replay_identical,
       (SELECT count(*) FROM run_match m WHERE m.run_id = r.id)     AS matches,
       (SELECT count(*) FROM run_exception e WHERE e.run_id = r.id) AS exceptions,
       CASE WHEN r.replay_of_run_id IS NULL THEN NULL
            WHEN r.status = 'running' THEN 'running'
            WHEN r.status = 'failed' THEN 'failed'
            WHEN r.result_sha256 IS NOT DISTINCT FROM o.result_sha256 THEN 'identical'
            ELSE 'different'
       END                                AS replay_outcome,
       encode(o.result_sha256, 'hex')     AS original_result_sha256,
       (SELECT count(*) FROM run_match m WHERE m.run_id = r.id AND m.pass = 'exact')       AS matches_exact,
       (SELECT count(*) FROM run_match m WHERE m.run_id = r.id AND m.pass = 'gross_net')   AS matches_gross_net,
       (SELECT count(*) FROM run_match m WHERE m.run_id = r.id AND m.pass = 'many_to_one') AS matches_many_to_one,
       (SELECT count(*) FROM exception_queue q WHERE q.run_id = r.id AND q.status = 'open') AS exceptions_open
  FROM reconciliation_run r
  JOIN import_file lf ON lf.id = r.ledger_file_id
  JOIN import_file sf ON sf.id = r.settlement_file_id
  JOIN import_file bf ON bf.id = r.bank_file_id
  LEFT JOIN reconciliation_run o ON o.id = r.replay_of_run_id;
