-- 0011: plain queue status for the UI (D-088).
--
-- The UI tells visitors when a queued job will run, and says so plainly when a job has waited
-- longer than the schedule allows, so a stopped worker is visible rather than silent. Whether a
-- job is "overdue" is a derived fact, so it is decided here, once, not in the UI (D-076).

-- When the job last entered the queue. created_at is not enough: a requeued job keeps its
-- original created_at, and would look overdue the moment it is requeued.
ALTER TABLE job ADD COLUMN queued_at timestamptz;
UPDATE job SET queued_at = created_at;
ALTER TABLE job ALTER COLUMN queued_at SET NOT NULL, ALTER COLUMN queued_at SET DEFAULT now();

-- How long a job may wait before the UI calls it overdue. The worker runs hourly at minute 7
-- (.github/workflows/worker.yml, D-087); a job queued just after a run waits at most about 60
-- minutes, plus the run's own time. 70 minutes leaves room for GitHub's scheduling delay without
-- hiding a worker that has stopped.
CREATE FUNCTION recon_queue_overdue_after() RETURNS interval
LANGUAGE sql IMMUTABLE AS $$ SELECT interval '70 minutes' $$;

-- requeue_failed_job (0009), unchanged except that a requeue restarts the queue clock.
CREATE OR REPLACE FUNCTION requeue_failed_job(p_job_id bigint, p_staff_id bigint) RETURNS text
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
  UPDATE job SET status = 'queued', error = NULL, started_at = NULL, finished_at = NULL,
                 queued_at = now()     -- attempts deliberately untouched
   WHERE id = v_job.id;
  RETURN 'requeued';
END;
$$;
-- CREATE OR REPLACE keeps the function's privileges (EXECUTE for recon_web only, from 0009).

-- Every job with its derived queue status. Read-only for both roles.
CREATE VIEW job_overview AS
SELECT j.id,
       j.kind,
       j.status,
       j.attempts,
       recon_max_job_attempts() AS max_attempts,
       j.error,
       j.import_file_id,
       j.ledger_file_id,
       j.settlement_file_id,
       j.bank_file_id,
       j.replay_of_run_id,
       j.run_id,
       j.created_at,
       j.queued_at,
       j.started_at,
       j.finished_at,
       (j.status = 'queued' AND j.queued_at < now() - recon_queue_overdue_after()) AS overdue,
       (SELECT count(*) FROM job_requeue q WHERE q.job_id = j.id) AS requeues
  FROM job j;

GRANT SELECT ON job_overview TO recon_web, recon_worker;
