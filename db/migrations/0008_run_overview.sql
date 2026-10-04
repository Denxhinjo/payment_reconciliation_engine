-- 0008: run overview with replay status (stage 6). See docs/design.md §6 and §9, D-068.
--
-- One row per run: engine version, the SHA-256 of each input file, timings, the result hash,
-- match and exception counts, and, for a replay, whether it reproduced the original exactly.
-- replay_identical is NULL for an original run, true when the replay finished with the same
-- result hash, false otherwise (a different hash, or a replay that failed).

CREATE VIEW run_overview AS
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
       (SELECT count(*) FROM run_exception e WHERE e.run_id = r.id) AS exceptions
  FROM reconciliation_run r
  JOIN import_file lf ON lf.id = r.ledger_file_id
  JOIN import_file sf ON sf.id = r.settlement_file_id
  JOIN import_file bf ON bf.id = r.bank_file_id
  LEFT JOIN reconciliation_run o ON o.id = r.replay_of_run_id;

GRANT SELECT ON run_overview TO recon_web, recon_worker;
