-- 0005: operational job queue for the Python worker (D-022). See docs/design.md §4.6.
-- This is operational state, not evidence: status updates are expected. Nothing about a
-- reconciliation's outcome is stored here.

CREATE TABLE job (
  id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind               text NOT NULL CHECK (kind IN ('parse_file', 'reconcile', 'replay')),
  import_file_id     bigint REFERENCES import_file (id),          -- parse_file
  ledger_file_id     bigint REFERENCES import_file (id),          -- reconcile
  settlement_file_id bigint REFERENCES import_file (id),          -- reconcile
  bank_file_id       bigint REFERENCES import_file (id),          -- reconcile
  replay_of_run_id   bigint REFERENCES reconciliation_run (id),   -- replay
  run_id             bigint REFERENCES reconciliation_run (id),   -- run produced, once known
  status             text NOT NULL DEFAULT 'queued'
                       CHECK (status IN ('queued', 'running', 'done', 'failed')),
  attempts           integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  error              text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  started_at         timestamptz,
  finished_at        timestamptz,
  -- Each kind carries exactly the arguments it needs, and no others.
  CHECK ((kind = 'parse_file') = (import_file_id IS NOT NULL)),
  CHECK ((kind = 'reconcile') = (num_nonnulls(ledger_file_id, settlement_file_id, bank_file_id) = 3)),
  CHECK (kind = 'reconcile' OR num_nonnulls(ledger_file_id, settlement_file_id, bank_file_id) = 0),
  CHECK ((kind = 'replay') = (replay_of_run_id IS NOT NULL)),
  -- A failed job always says why.
  CHECK (status <> 'failed' OR error IS NOT NULL)
);

-- An upload creates at most one parse job per file, so a duplicate upload (which hits
-- import_file_sha256_unique) can never queue a second parse.
CREATE UNIQUE INDEX job_one_parse_per_file ON job (import_file_id) WHERE kind = 'parse_file';

CREATE INDEX job_queued_idx ON job (id) WHERE status = 'queued';
