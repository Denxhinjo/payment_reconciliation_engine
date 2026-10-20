-- 0013: a queued job counts as overdue after 12 hours, not 70 minutes (D-095).
--
-- The worker is scheduled hourly (.github/workflows/worker.yml), but GitHub Actions delays and
-- drops scheduled runs. From 8 to 20 October 2026 it ran 60 times instead of about 300: the
-- median gap between runs was 5 hours and the longest 9.9 hours. At 70 minutes the UI told
-- visitors, most days, that a healthy worker "may be paused or failing". 12 hours is above every
-- gap seen, so the warning still exposes a worker that has really stopped.
--
-- Safe to run twice (CREATE OR REPLACE).
CREATE OR REPLACE FUNCTION recon_queue_overdue_after() RETURNS interval
LANGUAGE sql IMMUTABLE AS $$ SELECT interval '12 hours' $$;
