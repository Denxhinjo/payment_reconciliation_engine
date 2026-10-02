-- 0004: resolution reason codes and append-only resolutions.
-- See docs/design.md §4.5 and docs/decisions.md D-019, D-020.
--
-- SQLSTATEs raised here:
--   RC001  append-only table modified
--   RC004  resolution written against an exception whose run is not finished

-- Fixed list of reason codes. Seeded here and append-only, because resolutions cite them.
CREATE TABLE resolution_reason (
  code  text PRIMARY KEY CHECK (code ~ '^[a-z][a-z0-9_]*$'),
  label text NOT NULL CHECK (btrim(label) <> '')
);

INSERT INTO resolution_reason (code, label) VALUES
  ('timing_clears_next_period',       'Timing: clears in the next period'),
  ('processor_error_claim_raised',    'Processor error: claim raised with processor'),
  ('duplicate_ledger_entry_reversed', 'Duplicate ledger entry: reversed in ledger'),
  ('funds_identified_and_posted',     'Funds identified and posted'),
  ('funds_returned_to_sender',        'Funds returned to sender'),
  ('bank_error_raised',               'Bank error: raised with bank'),
  ('written_off_below_threshold',     'Written off: below threshold'),
  ('other_see_note',                  'Other: see note');

CREATE TRIGGER resolution_reason_append_only BEFORE UPDATE OR DELETE ON resolution_reason
  FOR EACH ROW EXECUTE FUNCTION reject_modification();
CREATE TRIGGER resolution_reason_no_truncate BEFORE TRUNCATE ON resolution_reason
  FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();

CREATE TABLE resolution (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  exception_id  bigint NOT NULL REFERENCES run_exception (id),
  -- A correction names the resolution it replaces; the first resolution has none.
  supersedes_id bigint,
  reason_code   text NOT NULL REFERENCES resolution_reason (code),
  -- Mandatory written note: at least 10 non-blank characters (D-020).
  note          text NOT NULL CHECK (char_length(btrim(note)) >= 10),
  resolved_by   bigint NOT NULL REFERENCES staff_user (id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, exception_id),
  -- A correction must supersede a resolution of the same exception.
  FOREIGN KEY (supersedes_id, exception_id) REFERENCES resolution (id, exception_id),
  -- Each resolution is superseded at most once, so each exception's history is one chain.
  CONSTRAINT resolution_superseded_once UNIQUE (supersedes_id)
);

-- Exactly one first resolution per exception. With resolution_superseded_once this makes
-- the history a single linear chain whose tail is the resolution in force.
CREATE UNIQUE INDEX resolution_one_root_per_exception
  ON resolution (exception_id) WHERE supersedes_id IS NULL;

CREATE FUNCTION resolution_require_finished_run() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_status text;
BEGIN
  SELECT r.status INTO v_status
    FROM run_exception e JOIN reconciliation_run r ON r.id = e.run_id
   WHERE e.id = NEW.exception_id;
  IF v_status IS DISTINCT FROM 'finished' THEN
    RAISE EXCEPTION 'exception % belongs to a run that is %; only exceptions of finished runs can be resolved',
      NEW.exception_id, coalesce(v_status, 'missing')
      USING ERRCODE = 'RC004';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER resolution_require_finished_run BEFORE INSERT ON resolution
  FOR EACH ROW EXECUTE FUNCTION resolution_require_finished_run();
CREATE TRIGGER resolution_append_only BEFORE UPDATE OR DELETE ON resolution
  FOR EACH ROW EXECUTE FUNCTION reject_modification();
CREATE TRIGGER resolution_no_truncate BEFORE TRUNCATE ON resolution
  FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();

-- The resolution currently in force for each exception: the tail of its chain.
CREATE VIEW current_resolution AS
SELECT r.*
  FROM resolution r
 WHERE NOT EXISTS (SELECT 1 FROM resolution n WHERE n.supersedes_id = r.id);
