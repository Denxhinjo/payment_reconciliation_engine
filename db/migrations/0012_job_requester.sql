-- 0012: who asked for each job, and a system actor for seeded data (D-091).
--
-- Every job records a requester (a staff_user). Seeded data is attributed to a clearly labelled
-- system actor, "Deployment seed", which can never sign in, instead of to a demo person.
-- Enqueueing moves into three database functions, upload_file(), enqueue_reconcile() and
-- enqueue_replay(), so the web app and the worker's seed command use literally the same code to
-- enqueue (they are written in different languages, so a shared function can only live here).
--
-- Safe to run twice: every statement is guarded (IF [NOT] EXISTS, ON CONFLICT, CREATE OR REPLACE).
--
-- SQLSTATEs raised here:
--   RC006  a job's requester was changed after it was recorded
--   RC007  jobs exist whose requester cannot be known; the migration refuses to invent one

-- --- System actor -------------------------------------------------------------------------------
-- 'system' is a staff role for things no person did. The web app refuses to sign in as it, and
-- treats a session cookie naming it as anonymous (lib/auth.ts, api/session/route.ts).
ALTER TABLE staff_user DROP CONSTRAINT IF EXISTS staff_user_role_check;
ALTER TABLE staff_user ADD CONSTRAINT staff_user_role_check
  CHECK (role IN ('analyst', 'controller', 'system'));

INSERT INTO staff_user (display_name, is_synthetic, role)
VALUES ('Deployment seed', true, 'system')
ON CONFLICT (display_name) DO NOTHING;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM staff_user WHERE display_name = 'Deployment seed' AND role <> 'system') THEN
    RAISE EXCEPTION 'a staff user named "Deployment seed" exists but is not the system actor';
  END IF;
END;
$$;

-- --- Requester on every job ------------------------------------------------------------------------
ALTER TABLE job ADD COLUMN IF NOT EXISTS requested_by bigint REFERENCES staff_user (id);

-- A parse job's requester is, truthfully, whoever uploaded its file.
UPDATE job j
   SET requested_by = f.uploaded_by
  FROM import_file f
 WHERE j.requested_by IS NULL AND j.kind = 'parse_file' AND f.id = j.import_file_id;

-- A reconcile or replay job queued before this migration has no knowable requester. Refuse
-- rather than invent one (fail closed); such a database needs a decision, not a guess.
DO $$
DECLARE
  v_unknown bigint;
BEGIN
  SELECT count(*) INTO v_unknown FROM job WHERE requested_by IS NULL;
  IF v_unknown > 0 THEN
    RAISE EXCEPTION '% job(s) have no recorded requester and none can be derived; refusing to invent one', v_unknown
      USING ERRCODE = 'RC007';
  END IF;
END;
$$;

ALTER TABLE job ALTER COLUMN requested_by SET NOT NULL;

-- The requester is recorded once and never changed, by the worker or anyone else.
CREATE OR REPLACE FUNCTION job_requester_is_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.requested_by IS DISTINCT FROM OLD.requested_by THEN
    RAISE EXCEPTION 'job %: the requester is recorded once and cannot be changed', OLD.id
      USING ERRCODE = 'RC006';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS job_requester_immutable ON job;
CREATE TRIGGER job_requester_immutable BEFORE UPDATE OF requested_by ON job
  FOR EACH ROW EXECUTE FUNCTION job_requester_is_immutable();

-- --- The one way to enqueue: shared by the web app and the worker's seed command --------------------

-- Store an uploaded file (idempotent by SHA-256, D-005) and queue its parse job, requested by the
-- uploader. Already-imported bytes create nothing and return the existing file.
CREATE OR REPLACE FUNCTION upload_file(p_kind file_kind, p_original_name text, p_raw bytea,
                                       p_uploaded_by bigint)
RETURNS TABLE (stored_file_id bigint, stored_kind file_kind, created boolean, parse_job_id bigint)
LANGUAGE plpgsql AS $$
DECLARE
  v_file bigint;
  v_kind file_kind;
  v_job  bigint;
BEGIN
  INSERT INTO import_file (kind, original_name, raw, uploaded_by)
  VALUES (p_kind, p_original_name, p_raw, p_uploaded_by)
  ON CONFLICT (sha256) DO NOTHING
  RETURNING id INTO v_file;
  IF v_file IS NULL THEN
    SELECT f.id, f.kind INTO v_file, v_kind FROM import_file f WHERE f.sha256 = sha256(p_raw);
    RETURN QUERY SELECT v_file, v_kind, false, NULL::bigint;
    RETURN;
  END IF;
  INSERT INTO job (kind, import_file_id, requested_by)
  VALUES ('parse_file', v_file, p_uploaded_by)
  RETURNING id INTO v_job;
  RETURN QUERY SELECT v_file, p_kind, true, v_job;
END;
$$;

-- Queue a reconciliation. Returns the new job id, or NULL if one for the same three files is
-- already queued or running (D-075).
CREATE OR REPLACE FUNCTION enqueue_reconcile(p_ledger_file_id bigint, p_settlement_file_id bigint,
                                             p_bank_file_id bigint, p_requested_by bigint)
RETURNS bigint
LANGUAGE sql AS $$
  INSERT INTO job (kind, ledger_file_id, settlement_file_id, bank_file_id, requested_by)
  VALUES ('reconcile', p_ledger_file_id, p_settlement_file_id, p_bank_file_id, p_requested_by)
  ON CONFLICT (ledger_file_id, settlement_file_id, bank_file_id)
    WHERE kind = 'reconcile' AND status IN ('queued', 'running') DO NOTHING
  RETURNING id
$$;

-- Queue a replay. Returns the new job id, or NULL if a replay of that run is already pending.
CREATE OR REPLACE FUNCTION enqueue_replay(p_run_id bigint, p_requested_by bigint)
RETURNS bigint
LANGUAGE sql AS $$
  INSERT INTO job (kind, replay_of_run_id, requested_by)
  VALUES ('replay', p_run_id, p_requested_by)
  ON CONFLICT (replay_of_run_id) WHERE kind = 'replay' AND status IN ('queued', 'running') DO NOTHING
  RETURNING id
$$;

-- They run as the caller (both roles already hold INSERT on import_file and job); only the two
-- application roles may call them.
REVOKE ALL ON FUNCTION upload_file(file_kind, text, bytea, bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION enqueue_reconcile(bigint, bigint, bigint, bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION enqueue_replay(bigint, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION upload_file(file_kind, text, bytea, bigint) TO recon_web, recon_worker;
GRANT EXECUTE ON FUNCTION enqueue_reconcile(bigint, bigint, bigint, bigint) TO recon_web, recon_worker;
GRANT EXECUTE ON FUNCTION enqueue_replay(bigint, bigint) TO recon_web, recon_worker;

-- --- Views: requester names, with their role, for display ------------------------------------------
-- Columns are appended (CREATE OR REPLACE VIEW may only add columns at the end); the rest of each
-- definition is unchanged from 0011 and 0009.

CREATE OR REPLACE VIEW job_overview AS
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
       (SELECT count(*) FROM job_requeue q WHERE q.job_id = j.id) AS requeues,
       j.requested_by,
       s.display_name AS requested_by_name,
       s.role         AS requested_by_role
  FROM job j
  JOIN staff_user s ON s.id = j.requested_by;

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
       (SELECT count(*) FROM exception_queue q WHERE q.run_id = r.id AND q.status = 'open') AS exceptions_open,
       -- Who asked for this run: the requester of the job that produced it. NULL for a run made
       -- outside the job queue (the CLI's `recon reconcile`).
       (SELECT s.display_name FROM job j JOIN staff_user s ON s.id = j.requested_by
         WHERE j.run_id = r.id ORDER BY j.id LIMIT 1) AS requested_by_name,
       (SELECT s.role FROM job j JOIN staff_user s ON s.id = j.requested_by
         WHERE j.run_id = r.id ORDER BY j.id LIMIT 1) AS requested_by_role
  FROM reconciliation_run r
  JOIN import_file lf ON lf.id = r.ledger_file_id
  JOIN import_file sf ON sf.id = r.settlement_file_id
  JOIN import_file bf ON bf.id = r.bank_file_id
  LEFT JOIN reconciliation_run o ON o.id = r.replay_of_run_id;
