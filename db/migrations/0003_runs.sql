-- 0003: reconciliation runs, matches, exceptions and allocations.
-- See docs/design.md §4.3–4.4 and docs/decisions.md D-010, D-011, D-012, D-033.
--
-- SQLSTATEs raised here:
--   RC001  append-only table modified (from reject_modification, 0001)
--   RC002  illegal run state change, or a write against a run that is not 'running'
--   RC003  finish check failed: incomplete allocation, empty match/exception, or a match
--          whose shape or arithmetic does not hold for its pass

CREATE TABLE reconciliation_run (
  id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  engine_version     text NOT NULL CHECK (engine_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
  engine_git_sha     text,                     -- provenance only; not part of the result
  ledger_file_id     bigint NOT NULL,
  ledger_kind        file_kind GENERATED ALWAYS AS ('ledger'::file_kind) STORED,
  settlement_file_id bigint NOT NULL,
  settlement_kind    file_kind GENERATED ALWAYS AS ('settlement'::file_kind) STORED,
  bank_file_id       bigint NOT NULL,
  bank_kind          file_kind GENERATED ALWAYS AS ('bank'::file_kind) STORED,
  parse_status       text GENERATED ALWAYS AS ('parsed') STORED,
  replay_of_run_id   bigint REFERENCES reconciliation_run (id),
  status             text NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'finished', 'failed')),
  error              text,
  started_at         timestamptz NOT NULL DEFAULT now(),
  finished_at        timestamptz,
  result_canonical   bytea,
  result_sha256      bytea GENERATED ALWAYS AS (sha256(result_canonical)) STORED,
  -- Each slot must hold a successfully parsed file of the right kind. A bank file cannot be
  -- put in the ledger slot, and a rejected or unparsed file cannot be used at all.
  FOREIGN KEY (ledger_file_id, ledger_kind, parse_status)
    REFERENCES import_parse (import_file_id, kind, status),
  FOREIGN KEY (settlement_file_id, settlement_kind, parse_status)
    REFERENCES import_parse (import_file_id, kind, status),
  FOREIGN KEY (bank_file_id, bank_kind, parse_status)
    REFERENCES import_parse (import_file_id, kind, status),
  -- A finished run always has its canonical result; nothing else does.
  CHECK ((status = 'finished') = (result_canonical IS NOT NULL)),
  CHECK ((status = 'running') = (finished_at IS NULL)),
  -- A failed run always says why.
  CHECK ((status = 'failed') = (error IS NOT NULL)),
  -- Targets for allocation foreign keys: an allocated row must come from this run's file.
  UNIQUE (id, ledger_file_id),
  UNIQUE (id, settlement_file_id),
  UNIQUE (id, bank_file_id)
);

CREATE TABLE run_match (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id      bigint NOT NULL REFERENCES reconciliation_run (id),
  ordinal     integer NOT NULL CHECK (ordinal >= 1),   -- position in the canonical result
  pass        text NOT NULL CHECK (pass IN ('exact', 'gross_net', 'many_to_one')),
  explanation text NOT NULL CHECK (btrim(explanation) <> ''),
  UNIQUE (run_id, ordinal),
  UNIQUE (id, run_id)
);

CREATE TABLE run_exception (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id           bigint NOT NULL REFERENCES reconciliation_run (id),
  ordinal          integer NOT NULL CHECK (ordinal >= 1),
  suggested_reason text NOT NULL CHECK (suggested_reason IN (
                     'missing_from_bank', 'amount_mismatch', 'timing', 'unknown_deposit',
                     'possible_duplicate', 'missing_from_ledger', 'unexplained_debit')),
  explanation      text NOT NULL CHECK (btrim(explanation) <> ''),
  -- sha256(reason || sorted natural keys of its rows); stored for later cross-run use (D-021).
  fingerprint      bytea NOT NULL CHECK (octet_length(fingerprint) = 32),
  UNIQUE (run_id, ordinal),
  UNIQUE (id, run_id)
);

-- One allocation table per input kind (D-010). For every table:
--   PRIMARY KEY (run_id, row)        a row is consumed at most once per run
--   num_nonnulls(...) = 1            a row is matched OR an exception, never both or neither
--   FK (run_id, file) -> run         the row's file is this run's file for that kind (D-011)
--   FK (row, file)    -> row table   ... and the row really belongs to that file
--   FK (match, run)   -> run_match   the match belongs to the same run

CREATE TABLE allocation_ledger (
  run_id          bigint NOT NULL,
  ledger_file_id  bigint NOT NULL,
  ledger_entry_id bigint NOT NULL,
  match_id        bigint,
  exception_id    bigint,
  PRIMARY KEY (run_id, ledger_entry_id),
  CHECK (num_nonnulls(match_id, exception_id) = 1),
  FOREIGN KEY (run_id, ledger_file_id) REFERENCES reconciliation_run (id, ledger_file_id),
  FOREIGN KEY (ledger_entry_id, ledger_file_id) REFERENCES ledger_entry (id, import_file_id),
  FOREIGN KEY (match_id, run_id) REFERENCES run_match (id, run_id),
  FOREIGN KEY (exception_id, run_id) REFERENCES run_exception (id, run_id)
);

CREATE TABLE allocation_settlement (
  run_id             bigint NOT NULL,
  settlement_file_id bigint NOT NULL,
  settlement_line_id bigint NOT NULL,
  match_id           bigint,
  exception_id       bigint,
  PRIMARY KEY (run_id, settlement_line_id),
  CHECK (num_nonnulls(match_id, exception_id) = 1),
  FOREIGN KEY (run_id, settlement_file_id)
    REFERENCES reconciliation_run (id, settlement_file_id),
  FOREIGN KEY (settlement_line_id, settlement_file_id)
    REFERENCES settlement_line (id, import_file_id),
  FOREIGN KEY (match_id, run_id) REFERENCES run_match (id, run_id),
  FOREIGN KEY (exception_id, run_id) REFERENCES run_exception (id, run_id)
);

CREATE TABLE allocation_bank (
  run_id        bigint NOT NULL,
  bank_file_id  bigint NOT NULL,
  bank_entry_id bigint NOT NULL,
  match_id      bigint,
  exception_id  bigint,
  PRIMARY KEY (run_id, bank_entry_id),
  CHECK (num_nonnulls(match_id, exception_id) = 1),
  FOREIGN KEY (run_id, bank_file_id) REFERENCES reconciliation_run (id, bank_file_id),
  FOREIGN KEY (bank_entry_id, bank_file_id) REFERENCES bank_entry (id, import_file_id),
  FOREIGN KEY (match_id, run_id) REFERENCES run_match (id, run_id),
  FOREIGN KEY (exception_id, run_id) REFERENCES run_exception (id, run_id)
);

CREATE INDEX allocation_ledger_match_idx ON allocation_ledger (match_id) WHERE match_id IS NOT NULL;
CREATE INDEX allocation_ledger_exception_idx ON allocation_ledger (exception_id) WHERE exception_id IS NOT NULL;
CREATE INDEX allocation_settlement_match_idx ON allocation_settlement (match_id) WHERE match_id IS NOT NULL;
CREATE INDEX allocation_settlement_exception_idx ON allocation_settlement (exception_id) WHERE exception_id IS NOT NULL;
CREATE INDEX allocation_bank_match_idx ON allocation_bank (match_id) WHERE match_id IS NOT NULL;
CREATE INDEX allocation_bank_exception_idx ON allocation_bank (exception_id) WHERE exception_id IS NOT NULL;

-- ---------------------------------------------------------------------------------------
-- Run lifecycle: inserted as 'running', then exactly one transition to 'finished' or
-- 'failed'. Nothing else about a run may change, and runs are never deleted.
-- ---------------------------------------------------------------------------------------

CREATE FUNCTION run_before_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status <> 'running' THEN
    RAISE EXCEPTION 'a run must be created in status running, not %', NEW.status
      USING ERRCODE = 'RC002';
  END IF;
  RETURN NEW;
END;
$$;

CREATE FUNCTION run_before_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status <> 'running' THEN
    RAISE EXCEPTION 'run % is % and can no longer change', OLD.id, OLD.status
      USING ERRCODE = 'RC002';
  END IF;
  IF NEW.status = 'running' THEN
    RAISE EXCEPTION 'run %: the only permitted update is running -> finished or failed', OLD.id
      USING ERRCODE = 'RC002';
  END IF;
  IF (NEW.engine_version, NEW.engine_git_sha, NEW.ledger_file_id, NEW.settlement_file_id,
      NEW.bank_file_id, NEW.replay_of_run_id, NEW.started_at)
     IS DISTINCT FROM
     (OLD.engine_version, OLD.engine_git_sha, OLD.ledger_file_id, OLD.settlement_file_id,
      OLD.bank_file_id, OLD.replay_of_run_id, OLD.started_at) THEN
    RAISE EXCEPTION 'run %: identity columns of a run cannot change', OLD.id
      USING ERRCODE = 'RC002';
  END IF;
  RETURN NEW;
END;
$$;

-- Independent check of the engine's output, run inside the database when a run finishes
-- (D-012). Raises RC003 and so aborts the transition if any invariant fails.
CREATE FUNCTION check_run_finish(p_run_id bigint) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  v_run     reconciliation_run%ROWTYPE;
  v_missing bigint;
  v_bad     record;
BEGIN
  SELECT * INTO v_run FROM reconciliation_run WHERE id = p_run_id;

  -- 1. Completeness: every row of each input file has an allocation in this run. Together
  --    with the allocation primary keys this makes "exactly once".
  SELECT count(*) INTO v_missing FROM ledger_entry le
   WHERE le.import_file_id = v_run.ledger_file_id
     AND NOT EXISTS (SELECT 1 FROM allocation_ledger a
                      WHERE a.run_id = v_run.id AND a.ledger_entry_id = le.id);
  IF v_missing > 0 THEN
    RAISE EXCEPTION 'run %: % ledger row(s) have no allocation', v_run.id, v_missing
      USING ERRCODE = 'RC003';
  END IF;

  SELECT count(*) INTO v_missing FROM settlement_line sl
   WHERE sl.import_file_id = v_run.settlement_file_id
     AND NOT EXISTS (SELECT 1 FROM allocation_settlement a
                      WHERE a.run_id = v_run.id AND a.settlement_line_id = sl.id);
  IF v_missing > 0 THEN
    RAISE EXCEPTION 'run %: % settlement row(s) have no allocation', v_run.id, v_missing
      USING ERRCODE = 'RC003';
  END IF;

  SELECT count(*) INTO v_missing FROM bank_entry be
   WHERE be.import_file_id = v_run.bank_file_id
     AND NOT EXISTS (SELECT 1 FROM allocation_bank a
                      WHERE a.run_id = v_run.id AND a.bank_entry_id = be.id);
  IF v_missing > 0 THEN
    RAISE EXCEPTION 'run %: % bank row(s) have no allocation', v_run.id, v_missing
      USING ERRCODE = 'RC003';
  END IF;

  -- 2. Every match and every exception cites at least one input row.
  SELECT m.ordinal INTO v_bad FROM run_match m
   WHERE m.run_id = v_run.id
     AND NOT EXISTS (SELECT 1 FROM allocation_ledger a WHERE a.match_id = m.id)
     AND NOT EXISTS (SELECT 1 FROM allocation_settlement a WHERE a.match_id = m.id)
     AND NOT EXISTS (SELECT 1 FROM allocation_bank a WHERE a.match_id = m.id)
   ORDER BY m.ordinal LIMIT 1;
  IF FOUND THEN
    RAISE EXCEPTION 'run %: match #% cites no input rows', v_run.id, v_bad.ordinal
      USING ERRCODE = 'RC003';
  END IF;

  SELECT e.ordinal INTO v_bad FROM run_exception e
   WHERE e.run_id = v_run.id
     AND NOT EXISTS (SELECT 1 FROM allocation_ledger a WHERE a.exception_id = e.id)
     AND NOT EXISTS (SELECT 1 FROM allocation_settlement a WHERE a.exception_id = e.id)
     AND NOT EXISTS (SELECT 1 FROM allocation_bank a WHERE a.exception_id = e.id)
   ORDER BY e.ordinal LIMIT 1;
  IF FOUND THEN
    RAISE EXCEPTION 'run %: exception #% cites no input rows', v_run.id, v_bad.ordinal
      USING ERRCODE = 'RC003';
  END IF;

  -- 3. Every match has the shape and arithmetic its pass claims (design §4.3), and all its
  --    rows share one currency.
  WITH m AS (
    SELECT id, ordinal, pass FROM run_match WHERE run_id = v_run.id
  ), l AS (
    SELECT a.match_id,
           count(*)                AS cnt,
           sum(le.amount_minor)    AS amount,
           min(le.currency)        AS ccy_min,
           max(le.currency)        AS ccy_max,
           array_agg(btrim(le.payment_reference) ORDER BY btrim(le.payment_reference)) AS refs
      FROM allocation_ledger a JOIN ledger_entry le ON le.id = a.ledger_entry_id
     WHERE a.run_id = v_run.id AND a.match_id IS NOT NULL
     GROUP BY a.match_id
  ), s AS (
    SELECT a.match_id,
           count(*)                              AS cnt,
           sum(sl.gross_minor)                   AS gross,
           sum(sl.fee_minor)                     AS fee,
           sum(sl.net_minor)                     AS net,
           min(sl.currency)                      AS ccy_min,
           max(sl.currency)                      AS ccy_max,
           count(DISTINCT btrim(sl.payout_reference)) AS payout_count,
           min(btrim(sl.payout_reference))       AS payout_ref,
           array_agg(btrim(sl.merchant_reference) ORDER BY btrim(sl.merchant_reference)) AS refs
      FROM allocation_settlement a JOIN settlement_line sl ON sl.id = a.settlement_line_id
     WHERE a.run_id = v_run.id AND a.match_id IS NOT NULL
     GROUP BY a.match_id
  ), b AS (
    SELECT a.match_id,
           count(*)                       AS cnt,
           sum(be.amount_minor)           AS amount,
           min(be.amount_minor)           AS min_amount,
           min(be.currency)               AS ccy_min,
           max(be.currency)               AS ccy_max,
           min(btrim(be.end_to_end_id))   AS end_to_end_id,
           min(btrim(be.remittance_ustrd)) AS ustrd
      FROM allocation_bank a JOIN bank_entry be ON be.id = a.bank_entry_id
     WHERE a.run_id = v_run.id AND a.match_id IS NOT NULL
     GROUP BY a.match_id
  ), c AS (
    SELECT m.ordinal, m.pass,
           coalesce(l.cnt, 0) AS nl, coalesce(s.cnt, 0) AS ns, coalesce(b.cnt, 0) AS nb,
           l.amount AS ledger_amount, l.refs AS ledger_refs,
           s.gross, s.fee, s.net, s.payout_count, s.payout_ref, s.refs AS settlement_refs,
           b.amount AS bank_amount, b.min_amount AS bank_min_amount,
           b.end_to_end_id, b.ustrd,
           array_remove(ARRAY[l.ccy_min, l.ccy_max, s.ccy_min, s.ccy_max,
                              b.ccy_min, b.ccy_max]::text[], NULL) AS currencies
      FROM m
      LEFT JOIN l ON l.match_id = m.id
      LEFT JOIN s ON s.match_id = m.id
      LEFT JOIN b ON b.match_id = m.id
  )
  SELECT c.ordinal, c.pass INTO v_bad FROM c
   WHERE NOT coalesce(
           (SELECT count(DISTINCT x) = 1 FROM unnest(c.currencies) AS x)
           AND CASE c.pass
             WHEN 'exact' THEN
                   c.nl = 1 AND c.ns = 0 AND c.nb = 1
               AND c.ledger_amount = c.bank_amount
               AND c.ledger_refs[1] = c.ustrd
             WHEN 'gross_net' THEN
                   c.nl = 1 AND c.ns = 1 AND c.nb = 1
               AND c.bank_min_amount > 0
               AND c.ledger_amount = c.gross
               AND c.bank_amount = c.net
               AND c.ledger_amount - c.bank_amount = c.fee
               AND c.ledger_refs = c.settlement_refs
               AND c.payout_ref = c.end_to_end_id
             WHEN 'many_to_one' THEN
                   c.nl >= 2 AND c.ns = c.nl AND c.nb = 1
               AND c.bank_min_amount > 0
               AND c.ledger_amount = c.gross
               AND c.bank_amount = c.net
               AND c.ledger_amount - c.bank_amount = c.fee
               AND c.ledger_refs = c.settlement_refs
               AND c.payout_count = 1
               AND c.payout_ref = c.end_to_end_id
           END,
           false)
   ORDER BY c.ordinal LIMIT 1;
  IF FOUND THEN
    RAISE EXCEPTION 'run %: match #% does not satisfy the % rule (shape, arithmetic, references or currency)',
      v_run.id, v_bad.ordinal, v_bad.pass
      USING ERRCODE = 'RC003';
  END IF;
END;
$$;

CREATE FUNCTION run_after_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'finished' THEN
    PERFORM check_run_finish(NEW.id);
  END IF;
  RETURN NULL;
END;
$$;

CREATE TRIGGER reconciliation_run_before_insert BEFORE INSERT ON reconciliation_run
  FOR EACH ROW EXECUTE FUNCTION run_before_insert();
CREATE TRIGGER reconciliation_run_before_update BEFORE UPDATE ON reconciliation_run
  FOR EACH ROW EXECUTE FUNCTION run_before_update();
CREATE TRIGGER reconciliation_run_after_update AFTER UPDATE ON reconciliation_run
  FOR EACH ROW EXECUTE FUNCTION run_after_update();
CREATE TRIGGER reconciliation_run_no_delete BEFORE DELETE ON reconciliation_run
  FOR EACH ROW EXECUTE FUNCTION reject_modification();
CREATE TRIGGER reconciliation_run_no_truncate BEFORE TRUNCATE ON reconciliation_run
  FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();

-- ---------------------------------------------------------------------------------------
-- Results of a run can only be written while it is running, and never changed afterwards.
-- FOR SHARE makes a concurrent finish wait until this insert's transaction ends, so the
-- finish check always sees every allocation written for the run.
-- ---------------------------------------------------------------------------------------

CREATE FUNCTION require_running_run() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_status text;
BEGIN
  SELECT status INTO v_status FROM reconciliation_run WHERE id = NEW.run_id FOR SHARE;
  IF v_status IS DISTINCT FROM 'running' THEN
    RAISE EXCEPTION 'cannot write to % for run %: run is %',
      TG_TABLE_NAME, NEW.run_id, coalesce(v_status, 'missing')
      USING ERRCODE = 'RC002';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER run_match_require_running BEFORE INSERT ON run_match
  FOR EACH ROW EXECUTE FUNCTION require_running_run();
CREATE TRIGGER run_exception_require_running BEFORE INSERT ON run_exception
  FOR EACH ROW EXECUTE FUNCTION require_running_run();
CREATE TRIGGER allocation_ledger_require_running BEFORE INSERT ON allocation_ledger
  FOR EACH ROW EXECUTE FUNCTION require_running_run();
CREATE TRIGGER allocation_settlement_require_running BEFORE INSERT ON allocation_settlement
  FOR EACH ROW EXECUTE FUNCTION require_running_run();
CREATE TRIGGER allocation_bank_require_running BEFORE INSERT ON allocation_bank
  FOR EACH ROW EXECUTE FUNCTION require_running_run();

CREATE TRIGGER run_match_append_only BEFORE UPDATE OR DELETE ON run_match
  FOR EACH ROW EXECUTE FUNCTION reject_modification();
CREATE TRIGGER run_match_no_truncate BEFORE TRUNCATE ON run_match
  FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();
CREATE TRIGGER run_exception_append_only BEFORE UPDATE OR DELETE ON run_exception
  FOR EACH ROW EXECUTE FUNCTION reject_modification();
CREATE TRIGGER run_exception_no_truncate BEFORE TRUNCATE ON run_exception
  FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();
CREATE TRIGGER allocation_ledger_append_only BEFORE UPDATE OR DELETE ON allocation_ledger
  FOR EACH ROW EXECUTE FUNCTION reject_modification();
CREATE TRIGGER allocation_ledger_no_truncate BEFORE TRUNCATE ON allocation_ledger
  FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();
CREATE TRIGGER allocation_settlement_append_only BEFORE UPDATE OR DELETE ON allocation_settlement
  FOR EACH ROW EXECUTE FUNCTION reject_modification();
CREATE TRIGGER allocation_settlement_no_truncate BEFORE TRUNCATE ON allocation_settlement
  FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();
CREATE TRIGGER allocation_bank_append_only BEFORE UPDATE OR DELETE ON allocation_bank
  FOR EACH ROW EXECUTE FUNCTION reject_modification();
CREATE TRIGGER allocation_bank_no_truncate BEFORE TRUNCATE ON allocation_bank
  FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();
