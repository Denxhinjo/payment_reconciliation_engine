-- 0014: the figures the redesigned UI shows, decided here, not in the UI (D-096, D-097).
--
-- The UI computes nothing (D-076). Every count, total and amount it shows comes from a view
-- below. Nothing here changes matching, a parser, a stored result or a rule the database
-- enforces: these are read-only views over what the engine already recorded, plus one table of
-- reference data describing the synthetic demo month.
--
-- Safe to run twice: CREATE OR REPLACE VIEW, CREATE TABLE IF NOT EXISTS, ON CONFLICT DO NOTHING.

-- --- Per exception: what each source contributed, and the amount at issue -------------------------
-- "Amount at issue" (D-097) is the money a break puts in question:
--   missing_from_bank    what should have reached the bank: settlement net, else the ledger amount
--   amount_mismatch      |expected at bank - received|; expected as above
--   possible_duplicate   the duplicate ledger amount
--   unknown_deposit      the bank credit
--   unexplained_debit    the bank debit, as a positive amount
--   missing_from_ledger  settlement gross with no ledger counterpart (gross - ledger)
--   timing               none: the money is expected to arrive after the period
CREATE OR REPLACE VIEW exception_figures AS
WITH l AS (
  SELECT a.exception_id, count(*) AS n, sum(le.amount_minor) AS total,
         min(le.entry_id COLLATE "C") AS first_entry, min(le.payment_reference COLLATE "C") AS first_reference
    FROM allocation_ledger a JOIN ledger_entry le ON le.id = a.ledger_entry_id
   WHERE a.exception_id IS NOT NULL GROUP BY a.exception_id
), s AS (
  SELECT a.exception_id, count(*) AS n, sum(sl.gross_minor) AS gross, sum(sl.fee_minor) AS fee,
         sum(sl.net_minor) AS net, min(sl.payout_id COLLATE "C") AS payout, count(DISTINCT sl.payout_id) AS payouts
    FROM allocation_settlement a JOIN settlement_line sl ON sl.id = a.settlement_line_id
   WHERE a.exception_id IS NOT NULL GROUP BY a.exception_id
), b AS (
  SELECT a.exception_id, count(*) AS n, sum(be.amount_minor) AS total,
         min(be.acct_svcr_ref COLLATE "C") AS first_reference
    FROM allocation_bank a JOIN bank_entry be ON be.id = a.bank_entry_id
   WHERE a.exception_id IS NOT NULL GROUP BY a.exception_id
)
SELECT e.id                                   AS exception_id,
       e.run_id,
       e.ordinal,
       e.suggested_reason,
       q.status,
       e.suggested_reason <> 'timing'         AS needs_review,
       coalesce(l.n, 0)                       AS ledger_rows,
       coalesce(s.n, 0)                       AS settlement_rows,
       coalesce(b.n, 0)                       AS bank_rows,
       l.total                                AS ledger_minor,
       s.gross                                AS settlement_gross_minor,
       s.fee                                  AS settlement_fee_minor,
       s.net                                  AS settlement_net_minor,
       b.total                                AS bank_minor,
       CASE WHEN e.suggested_reason = 'amount_mismatch' THEN coalesce(s.net, l.total) END AS expected_minor,
       CASE WHEN e.suggested_reason = 'amount_mismatch' THEN b.total END                  AS received_minor,
       -- positive: the bank received less than expected; negative: more
       CASE WHEN e.suggested_reason = 'amount_mismatch' THEN coalesce(s.net, l.total) - b.total END AS shortfall_minor,
       CASE e.suggested_reason
         WHEN 'missing_from_bank'   THEN coalesce(s.net, l.total)
         WHEN 'amount_mismatch'     THEN abs(coalesce(s.net, l.total) - coalesce(b.total, 0))
         WHEN 'possible_duplicate'  THEN l.total
         WHEN 'unknown_deposit'     THEN b.total
         WHEN 'unexplained_debit'   THEN abs(b.total)
         WHEN 'missing_from_ledger' THEN s.gross - coalesce(l.total, 0)
       END                                    AS amount_at_issue_minor,
       CASE WHEN s.payout IS NOT NULL THEN s.payout
            WHEN e.suggested_reason = 'possible_duplicate' THEN l.first_entry || ' · ' || l.first_reference
            WHEN l.first_reference IS NOT NULL THEN l.first_reference
            ELSE b.first_reference
       END                                    AS reference,
       coalesce(s.payouts, 0)                 AS payouts,
       (SELECT count(*) FROM exception_queue o
         WHERE o.run_id = e.run_id AND o.status = 'open' AND o.exception_id <> e.id) AS other_open_in_run
  FROM run_exception e
  JOIN exception_queue q ON q.exception_id = e.id
  LEFT JOIN l ON l.exception_id = e.id
  LEFT JOIN s ON s.exception_id = e.id
  LEFT JOIN b ON b.exception_id = e.id;

-- --- Per run: the statement period, the size of each input, and the queue at a glance -----------
CREATE OR REPLACE VIEW run_figures AS
SELECT r.id                                   AS run_id,
       st.period_from,
       st.period_to,
       -- "September 2026" for a whole calendar month, otherwise both dates. to_char without the TM
       -- prefix always spells English month names, whatever the server locale (cf. D-057).
       CASE WHEN st.period_from IS NULL THEN NULL
            WHEN st.period_from = date_trunc('month', st.period_from)::date
             AND st.period_to = (date_trunc('month', st.period_from) + interval '1 month - 1 day')::date
            THEN to_char(st.period_from, 'FMMonth YYYY')
            ELSE st.period_from::text || ' to ' || st.period_to::text
       END                                    AS period_label,
       st.currency,
       lf.original_name                       AS ledger_name,
       sf.original_name                       AS settlement_name,
       bf.original_name                       AS bank_name,
       (SELECT count(*) FROM ledger_entry x WHERE x.import_file_id = r.ledger_file_id)        AS ledger_rows,
       (SELECT count(*) FROM settlement_line x WHERE x.import_file_id = r.settlement_file_id) AS settlement_rows,
       (SELECT count(DISTINCT x.payout_id) FROM settlement_line x
         WHERE x.import_file_id = r.settlement_file_id)                                     AS settlement_payouts,
       (SELECT count(*) FROM bank_entry x WHERE x.import_file_id = r.bank_file_id)            AS bank_entries,
       (SELECT count(*) FROM ledger_entry x WHERE x.import_file_id = r.ledger_file_id)
         + (SELECT count(*) FROM settlement_line x WHERE x.import_file_id = r.settlement_file_id)
         + (SELECT count(*) FROM bank_entry x WHERE x.import_file_id = r.bank_file_id)        AS rows_compared,
       (SELECT count(*) FROM exception_figures f WHERE f.run_id = r.id AND f.status = 'open' AND f.needs_review)     AS open_needing_review,
       (SELECT count(*) FROM exception_figures f WHERE f.run_id = r.id AND f.status = 'open' AND NOT f.needs_review) AS open_timing,
       (SELECT count(*) FROM exception_figures f WHERE f.run_id = r.id AND f.status = 'resolved')                    AS resolved,
       (SELECT coalesce(sum(f.amount_at_issue_minor), 0) FROM exception_figures f
         WHERE f.run_id = r.id AND f.status = 'open' AND f.needs_review)                     AS open_value_under_review_minor,
       (SELECT x.id FROM reconciliation_run x WHERE x.replay_of_run_id = r.id ORDER BY x.id DESC LIMIT 1) AS latest_replay_run_id,
       (SELECT o.replay_outcome FROM run_overview o WHERE o.replay_of_run_id = r.id ORDER BY o.run_id DESC LIMIT 1) AS latest_replay_outcome
  FROM reconciliation_run r
  JOIN import_file lf ON lf.id = r.ledger_file_id
  JOIN import_file sf ON sf.id = r.settlement_file_id
  JOIN import_file bf ON bf.id = r.bank_file_id
  LEFT JOIN bank_statement st ON st.import_file_id = r.bank_file_id;

-- The run the dashboard and the exceptions page open on: the newest finished original run.
CREATE OR REPLACE VIEW latest_run AS
SELECT max(id) AS run_id FROM reconciliation_run WHERE replay_of_run_id IS NULL AND status = 'finished';

-- Exceptions by suggested reason, per run (the run page's "Exceptions raised").
CREATE OR REPLACE VIEW run_exception_reasons AS
SELECT run_id, suggested_reason, count(*) AS exceptions, min(ordinal) AS first_ordinal
  FROM run_exception GROUP BY run_id, suggested_reason;

-- --- Imported files, with their row counts --------------------------------------------------------
CREATE OR REPLACE VIEW import_file_overview AS
SELECT f.id                                   AS file_id,
       f.kind,
       f.original_name,
       encode(f.sha256, 'hex')                AS sha256,
       octet_length(f.raw)                    AS byte_size,
       s.display_name                         AS uploaded_by,
       s.role                                 AS uploaded_by_role,
       f.uploaded_at,
       p.status                               AS parse_status,
       p.error                                AS parse_error,
       p.parser_version,
       pj.status                              AS parse_job_status,
       pj.overdue                             AS parse_job_overdue,
       CASE WHEN p.status = 'parsed' THEN
         CASE f.kind WHEN 'ledger'     THEN (SELECT count(*) FROM ledger_entry x WHERE x.import_file_id = f.id)
                     WHEN 'settlement' THEN (SELECT count(*) FROM settlement_line x WHERE x.import_file_id = f.id)
                     WHEN 'bank'       THEN (SELECT count(*) FROM bank_entry x WHERE x.import_file_id = f.id)
         END
       END                                    AS parsed_rows
  FROM import_file f
  JOIN staff_user s ON s.id = f.uploaded_by
  LEFT JOIN import_parse p ON p.import_file_id = f.id
  LEFT JOIN job_overview pj ON pj.import_file_id = f.id AND pj.kind = 'parse_file';

CREATE OR REPLACE VIEW import_file_counts AS
SELECT count(*)                                          AS files,
       count(*) FILTER (WHERE parse_status = 'parsed')   AS parsed,
       count(*) FILTER (WHERE parse_status = 'rejected') AS rejected,
       count(*) FILTER (WHERE parse_status IS NULL)      AS awaiting_parse
  FROM import_file_overview;

-- --- Jobs: what each produced, and the queue at a glance ------------------------------------------
CREATE OR REPLACE VIEW job_results AS
SELECT j.id                                   AS job_id,
       f.kind                                 AS file_kind,
       f.parse_status                         AS file_parse_status,
       f.parsed_rows                          AS file_rows,
       r.status                               AS run_status,
       r.matches                              AS run_matches,
       r.exceptions                           AS run_exceptions,
       r.replay_outcome                       AS run_replay_outcome
  FROM job j
  LEFT JOIN import_file_overview f ON f.file_id = j.import_file_id
  LEFT JOIN run_overview r ON r.run_id = j.run_id;

CREATE OR REPLACE VIEW job_counts AS
SELECT count(*)                                   AS total,
       count(*) FILTER (WHERE status = 'queued')  AS queued,
       count(*) FILTER (WHERE status = 'running') AS running,
       count(*) FILTER (WHERE status = 'failed')  AS failed,
       count(*) FILTER (WHERE status = 'done')    AS done
  FROM job;

-- --- The demo month's planted problems, as reference data -----------------------------------------
-- What the synthetic generator deliberately put into the September 2026 demo month
-- (demo-data/2026-09/planted.json; a test keeps the two identical). Each item names the exact
-- rows involved and the outcome expected. An item applies only to a run of exactly these three
-- files, recognised by their SHA-256, so other uploads never show it.
CREATE TABLE IF NOT EXISTS demo_planted_item (
  item_id          text PRIMARY KEY,
  category         text NOT NULL CHECK (category IN ('planted', 'timing')),
  title            text NOT NULL,
  description      text NOT NULL,
  expected_kind    text NOT NULL CHECK (expected_kind IN ('exception', 'match')),
  expected_outcome text NOT NULL,
  ledger_keys      text[] NOT NULL,
  settlement_keys  text[] NOT NULL,
  bank_keys        text[] NOT NULL
);
CREATE TABLE IF NOT EXISTS demo_planted_files (
  ledger_sha256     bytea NOT NULL,
  settlement_sha256 bytea NOT NULL,
  bank_sha256       bytea NOT NULL,
  PRIMARY KEY (ledger_sha256, settlement_sha256, bank_sha256)
);
DROP TRIGGER IF EXISTS demo_planted_item_append_only ON demo_planted_item;
CREATE TRIGGER demo_planted_item_append_only BEFORE UPDATE OR DELETE ON demo_planted_item
  FOR EACH ROW EXECUTE FUNCTION reject_modification();
DROP TRIGGER IF EXISTS demo_planted_files_append_only ON demo_planted_files;
CREATE TRIGGER demo_planted_files_append_only BEFORE UPDATE OR DELETE ON demo_planted_files
  FOR EACH ROW EXECUTE FUNCTION reject_modification();

INSERT INTO demo_planted_files (ledger_sha256, settlement_sha256, bank_sha256) VALUES
  (decode('691de447dcb6aa0244acf155c0f919a0fc99898d4627d07082557f83dca749ef', 'hex'),
   decode('359e60947870726f9c038232007ee1261c44b423df042a6099e2daa5feabd100', 'hex'),
   decode('e9443591ce940db8382ffb0a80a95348817f64229393ea5a53de4731a27381ca', 'hex'))
ON CONFLICT DO NOTHING;

INSERT INTO demo_planted_item (item_id, category, title, description, expected_kind, expected_outcome,
                               ledger_keys, settlement_keys, bank_keys) VALUES
  ('P1', 'planted', 'Missing payout',
   'Payout PO-20260914 (9 payments, net EUR 1752.23, payout date 2026-09-16) is in the settlement report but never reaches the bank.',
   'exception', 'missing_from_bank',
   ARRAY['LE-000281', 'LE-000282', 'LE-000283', 'LE-000284', 'LE-000285', 'LE-000286', 'LE-000287', 'LE-000288', 'LE-000289'],
   ARRAY['BT-0000261', 'BT-0000262', 'BT-0000263', 'BT-0000264', 'BT-0000265', 'BT-0000266', 'BT-0000267', 'BT-0000268', 'BT-0000269'],
   ARRAY[]::text[]),
  ('P2', 'planted', 'Fee mismatch',
   'Single-payment payout PO-20260913: report states gross EUR 316.85, fee 4.69, net 312.16; the bank received 311.76 (0.40 less).',
   'exception', 'amount_mismatch',
   ARRAY['LE-000278'],
   ARRAY['BT-0000260'],
   ARRAY['SYNBANK-202609-000032']),
  ('P3', 'planted', '50-to-1 batch deposit',
   '50 card payments on 2026-09-16 are paid out as one bank credit of EUR 10624.87 (payout PO-20260916).',
   'match', 'many_to_one',
   ARRAY['LE-000316', 'LE-000317', 'LE-000318', 'LE-000319', 'LE-000320', 'LE-000321', 'LE-000322', 'LE-000323', 'LE-000324', 'LE-000325', 'LE-000326', 'LE-000327', 'LE-000328', 'LE-000329', 'LE-000330', 'LE-000331', 'LE-000332', 'LE-000333', 'LE-000334', 'LE-000335', 'LE-000336', 'LE-000337', 'LE-000338', 'LE-000339', 'LE-000340', 'LE-000341', 'LE-000342', 'LE-000343', 'LE-000344', 'LE-000345', 'LE-000346', 'LE-000347', 'LE-000348', 'LE-000349', 'LE-000350', 'LE-000351', 'LE-000352', 'LE-000353', 'LE-000354', 'LE-000355', 'LE-000356', 'LE-000357', 'LE-000358', 'LE-000359', 'LE-000360', 'LE-000361', 'LE-000362', 'LE-000363', 'LE-000364', 'LE-000365'],
   ARRAY['BT-0000294', 'BT-0000295', 'BT-0000296', 'BT-0000297', 'BT-0000298', 'BT-0000299', 'BT-0000300', 'BT-0000301', 'BT-0000302', 'BT-0000303', 'BT-0000304', 'BT-0000305', 'BT-0000306', 'BT-0000307', 'BT-0000308', 'BT-0000309', 'BT-0000310', 'BT-0000311', 'BT-0000312', 'BT-0000313', 'BT-0000314', 'BT-0000315', 'BT-0000316', 'BT-0000317', 'BT-0000318', 'BT-0000319', 'BT-0000320', 'BT-0000321', 'BT-0000322', 'BT-0000323', 'BT-0000324', 'BT-0000325', 'BT-0000326', 'BT-0000327', 'BT-0000328', 'BT-0000329', 'BT-0000330', 'BT-0000331', 'BT-0000332', 'BT-0000333', 'BT-0000334', 'BT-0000335', 'BT-0000336', 'BT-0000337', 'BT-0000338', 'BT-0000339', 'BT-0000340', 'BT-0000341', 'BT-0000342', 'BT-0000343'],
   ARRAY['SYNBANK-202609-000043']),
  ('P4', 'planted', 'Duplicate ledger entry',
   'Payment PAY-000520 (EUR 371.21) is recorded twice in the ledger: LE-000526 and LE-000527. The payment itself was collected and paid out once.',
   'exception', 'possible_duplicate',
   ARRAY['LE-000527'],
   ARRAY[]::text[],
   ARRAY[]::text[]),
  ('P5', 'planted', 'Deposit nobody expected',
   'A EUR 250.00 credit on 2026-09-22 with remittance text ''SYNTHETIC UNREFERENCED TRANSFER'' has no counterpart in the ledger or the report.',
   'exception', 'unknown_deposit',
   ARRAY[]::text[],
   ARRAY[]::text[],
   ARRAY['SYNBANK-202609-000049']),
  ('T1', 'timing', 'Expected timing difference',
   'Payout PO-20260929 for card payments on 2026-09-29 is dated 2026-10-01, after the statement period.',
   'exception', 'timing',
   ARRAY['LE-000616', 'LE-000617', 'LE-000618', 'LE-000619', 'LE-000620', 'LE-000621', 'LE-000622', 'LE-000623', 'LE-000624', 'LE-000625', 'LE-000626', 'LE-000627', 'LE-000628', 'LE-000629', 'LE-000630', 'LE-000631'],
   ARRAY['BT-0000576', 'BT-0000577', 'BT-0000578', 'BT-0000579', 'BT-0000580', 'BT-0000581', 'BT-0000582', 'BT-0000583', 'BT-0000584', 'BT-0000585', 'BT-0000586', 'BT-0000587', 'BT-0000588', 'BT-0000589', 'BT-0000590', 'BT-0000591'],
   ARRAY[]::text[]),
  ('T2', 'timing', 'Expected timing difference',
   'Payout PO-20260930 for card payments on 2026-09-30 is dated 2026-10-02, after the statement period.',
   'exception', 'timing',
   ARRAY['LE-000633', 'LE-000634', 'LE-000635', 'LE-000636', 'LE-000637', 'LE-000638', 'LE-000639', 'LE-000640', 'LE-000641', 'LE-000642', 'LE-000643', 'LE-000644', 'LE-000645', 'LE-000646', 'LE-000647', 'LE-000648', 'LE-000649', 'LE-000650', 'LE-000651', 'LE-000652', 'LE-000653'],
   ARRAY['BT-0000592', 'BT-0000593', 'BT-0000594', 'BT-0000595', 'BT-0000596', 'BT-0000597', 'BT-0000598', 'BT-0000599', 'BT-0000600', 'BT-0000601', 'BT-0000602', 'BT-0000603', 'BT-0000604', 'BT-0000605', 'BT-0000606', 'BT-0000607', 'BT-0000608', 'BT-0000609', 'BT-0000610', 'BT-0000611', 'BT-0000612'],
   ARRAY[]::text[]),
  ('T3', 'timing', 'Expected timing difference',
   'Bank transfer PAY-000625 booked in the ledger on 2026-09-30 arrives at the bank after the statement period.',
   'exception', 'timing',
   ARRAY['LE-000632'],
   ARRAY[]::text[],
   ARRAY[]::text[])
ON CONFLICT (item_id) DO NOTHING;

-- Per run of the demo files: each planted item, and whether the run produced exactly the expected
-- outcome. "Caught" is decided by data, not asserted: the run must hold an exception with the
-- expected reason (or a match with the expected pass) whose rows are exactly the listed rows.
CREATE OR REPLACE VIEW run_planted_items AS
WITH demo_runs AS (
  SELECT r.id AS run_id
    FROM reconciliation_run r
    JOIN import_file lf ON lf.id = r.ledger_file_id
    JOIN import_file sf ON sf.id = r.settlement_file_id
    JOIN import_file bf ON bf.id = r.bank_file_id
    JOIN demo_planted_files d ON d.ledger_sha256 = lf.sha256 AND d.settlement_sha256 = sf.sha256
                             AND d.bank_sha256 = bf.sha256
   WHERE r.status = 'finished'
), outcomes AS (
  SELECT 'exception' AS kind, e.run_id, e.id AS outcome_id, e.ordinal, e.suggested_reason AS outcome,
         coalesce((SELECT array_agg(le.entry_id ORDER BY le.entry_id COLLATE "C")
                     FROM allocation_ledger a JOIN ledger_entry le ON le.id = a.ledger_entry_id
                    WHERE a.exception_id = e.id), ARRAY[]::text[]) AS ledger_keys,
         coalesce((SELECT array_agg(sl.balance_transaction_id ORDER BY sl.balance_transaction_id COLLATE "C")
                     FROM allocation_settlement a JOIN settlement_line sl ON sl.id = a.settlement_line_id
                    WHERE a.exception_id = e.id), ARRAY[]::text[]) AS settlement_keys,
         coalesce((SELECT array_agg(be.acct_svcr_ref ORDER BY be.acct_svcr_ref COLLATE "C")
                     FROM allocation_bank a JOIN bank_entry be ON be.id = a.bank_entry_id
                    WHERE a.exception_id = e.id), ARRAY[]::text[]) AS bank_keys
    FROM run_exception e JOIN demo_runs d ON d.run_id = e.run_id
  UNION ALL
  SELECT 'match', m.run_id, m.id, m.ordinal, m.pass,
         coalesce((SELECT array_agg(le.entry_id ORDER BY le.entry_id COLLATE "C")
                     FROM allocation_ledger a JOIN ledger_entry le ON le.id = a.ledger_entry_id
                    WHERE a.match_id = m.id), ARRAY[]::text[]),
         coalesce((SELECT array_agg(sl.balance_transaction_id ORDER BY sl.balance_transaction_id COLLATE "C")
                     FROM allocation_settlement a JOIN settlement_line sl ON sl.id = a.settlement_line_id
                    WHERE a.match_id = m.id), ARRAY[]::text[]),
         coalesce((SELECT array_agg(be.acct_svcr_ref ORDER BY be.acct_svcr_ref COLLATE "C")
                     FROM allocation_bank a JOIN bank_entry be ON be.id = a.bank_entry_id
                    WHERE a.match_id = m.id), ARRAY[]::text[])
    FROM run_match m JOIN demo_runs d ON d.run_id = m.run_id
)
SELECT d.run_id,
       p.item_id,
       p.category,
       p.title,
       p.description,
       p.expected_kind,
       p.expected_outcome,
       o.outcome_id IS NOT NULL               AS caught,
       CASE WHEN o.kind = 'exception' THEN o.outcome_id END AS exception_id,
       o.ordinal                              AS outcome_ordinal
  FROM demo_runs d
  CROSS JOIN demo_planted_item p
  LEFT JOIN outcomes o ON o.run_id = d.run_id AND o.kind = p.expected_kind AND o.outcome = p.expected_outcome
                      AND o.ledger_keys = p.ledger_keys AND o.settlement_keys = p.settlement_keys
                      AND o.bank_keys = p.bank_keys;

-- --- Grants: every new relation is read-only for both application roles --------------------------
GRANT SELECT ON exception_figures, run_figures, latest_run, run_exception_reasons, import_file_overview,
                import_file_counts, job_results, job_counts, demo_planted_item, demo_planted_files,
                run_planted_items
  TO recon_web, recon_worker;
