-- 0007: the exceptions queue as a read-only view (stage 5). See docs/design.md §4.5, D-065.
--
-- One row per exception with its status: 'open' until a first resolution exists, 'resolved'
-- after. The resolution shown is the one in force: the tail of the append-only chain
-- (current_resolution, 0004). The view writes nothing; resolutions are only ever inserted into
-- `resolution`, under that table's constraints and triggers.

CREATE VIEW exception_queue AS
SELECT e.id                 AS exception_id,
       e.run_id,
       e.ordinal,
       e.suggested_reason,
       e.explanation,
       CASE WHEN c.id IS NULL THEN 'open' ELSE 'resolved' END AS status,
       c.id                 AS current_resolution_id,
       c.reason_code        AS current_reason_code,
       c.note               AS current_note,
       s.display_name       AS resolved_by,
       c.created_at         AS resolved_at,
       (SELECT count(*) FROM resolution x WHERE x.exception_id = e.id) AS resolution_count
  FROM run_exception e
  LEFT JOIN current_resolution c ON c.exception_id = e.id
  LEFT JOIN staff_user s ON s.id = c.resolved_by;

-- Views created after 0006 are not covered by its GRANT ... ON ALL TABLES; grant explicitly.
-- (The role matrix test fails if a relation's privileges differ from the intended matrix.)
GRANT SELECT ON exception_queue TO recon_web, recon_worker;

-- Views are read-only for both roles. 0006's GRANT INSERT ON ALL TABLES also reached the
-- current_resolution view, which PostgreSQL makes auto-updatable, giving the worker a second
-- path for inserting resolutions. Close it: resolutions are inserted into `resolution` only.
REVOKE INSERT ON current_resolution FROM recon_worker;
