-- 0006: database roles for the web app and the worker. Defence in depth on top of the
-- append-only triggers (D-019): the web role cannot UPDATE or DELETE any evidence table, so
-- even a trigger dropped by mistake would not open an edit path through the app.
--
-- These are NOLOGIN group roles. The login users the apps connect as are created outside
-- migrations (they carry passwords) and are granted membership, e.g.
--   CREATE ROLE recon_web_login LOGIN PASSWORD '...' IN ROLE recon_web;
-- Roles are cluster-wide, so creation is idempotent.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'recon_web') THEN
    CREATE ROLE recon_web NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'recon_worker') THEN
    CREATE ROLE recon_worker NOLOGIN;
  END IF;
END;
$$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;

GRANT USAGE ON SCHEMA public TO recon_web, recon_worker;

-- Web: reads everything; writes only uploads, job requests and resolutions. No UPDATE or
-- DELETE anywhere.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO recon_web;
GRANT INSERT ON import_file, job, resolution TO recon_web;

-- Worker: reads and inserts everything it produces; may update only the two tables whose
-- lifecycle is a status (runs, guarded by trigger; jobs, operational). No DELETE anywhere.
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA public TO recon_worker;
GRANT UPDATE ON reconciliation_run, job TO recon_worker;

-- The migration ledger belongs to the migration runner only.
REVOKE ALL ON schema_migration FROM recon_web, recon_worker;
