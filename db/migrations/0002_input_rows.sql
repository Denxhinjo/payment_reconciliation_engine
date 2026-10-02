-- 0002: parsed input rows from the three sources.
-- See docs/design.md §4.2. Rows are written once by the parser and never changed, because
-- reconciliation runs cite them.
--
-- Every row table carries two generated constant columns, file_kind and parse_status, so that
-- one composite foreign key to import_parse proves the row came from a file of the right kind
-- whose parse outcome is 'parsed' (D-032). A rejected file can never have rows.

CREATE TABLE ledger_entry (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  import_file_id    bigint NOT NULL,
  file_kind         file_kind GENERATED ALWAYS AS ('ledger'::file_kind) STORED,
  parse_status      text GENERATED ALWAYS AS ('parsed') STORED,
  row_number        integer NOT NULL CHECK (row_number >= 1),
  entry_id          text NOT NULL CHECK (btrim(entry_id) <> ''),
  booked_on         date NOT NULL,
  entry_type        text NOT NULL CHECK (entry_type IN ('payment', 'refund')),
  channel           text NOT NULL CHECK (channel IN ('card', 'bank_transfer')),
  payment_reference text NOT NULL CHECK (btrim(payment_reference) <> ''),
  amount_minor      bigint NOT NULL CHECK (amount_minor <> 0),
  currency          char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  customer_ref      text NOT NULL,
  description       text NOT NULL,
  FOREIGN KEY (import_file_id, file_kind, parse_status)
    REFERENCES import_parse (import_file_id, kind, status),
  -- Sign convention: positive = money in. The type and the sign must agree, so a refund
  -- recorded as a positive amount is rejected at import, not found at month end.
  CHECK ((entry_type = 'payment') = (amount_minor > 0)),
  -- Natural key used by the engine's output.
  UNIQUE (import_file_id, row_number),
  -- The ledger's own primary key may not repeat inside one export. (The planted duplicate is
  -- two different entry_ids for one payment, which must reach the engine.)
  UNIQUE (import_file_id, entry_id),
  -- Target for allocation foreign keys that tie a row to the run's own ledger file (D-011).
  UNIQUE (id, import_file_id)
);

CREATE TABLE settlement_line (
  id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  import_file_id         bigint NOT NULL,
  file_kind              file_kind GENERATED ALWAYS AS ('settlement'::file_kind) STORED,
  parse_status           text GENERATED ALWAYS AS ('parsed') STORED,
  row_number             integer NOT NULL CHECK (row_number >= 1),
  balance_transaction_id text NOT NULL CHECK (btrim(balance_transaction_id) <> ''),
  created_on             date NOT NULL,
  line_type              text NOT NULL CHECK (line_type IN ('charge', 'refund')),
  merchant_reference     text NOT NULL CHECK (btrim(merchant_reference) <> ''),
  currency               char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  gross_minor            bigint NOT NULL CHECK (gross_minor <> 0),
  fee_minor              bigint NOT NULL CHECK (fee_minor >= 0),
  net_minor              bigint NOT NULL,
  payout_id              text NOT NULL CHECK (btrim(payout_id) <> ''),
  payout_reference       text NOT NULL CHECK (btrim(payout_reference) <> ''),
  payout_date            date NOT NULL,
  FOREIGN KEY (import_file_id, file_kind, parse_status)
    REFERENCES import_parse (import_file_id, kind, status),
  -- A processor report that disagrees with itself is rejected; we only reconcile against
  -- documents that are internally consistent.
  CHECK (net_minor = gross_minor - fee_minor),
  CHECK ((line_type = 'charge') = (gross_minor > 0)),
  UNIQUE (import_file_id, row_number),
  UNIQUE (import_file_id, balance_transaction_id),
  UNIQUE (id, import_file_id)
);

-- One statement header per bank file (Level 1 rejects files with more than one Stmt).
CREATE TABLE bank_statement (
  import_file_id bigint PRIMARY KEY,
  file_kind      file_kind GENERATED ALWAYS AS ('bank'::file_kind) STORED,
  parse_status   text GENERATED ALWAYS AS ('parsed') STORED,
  msg_id         text NOT NULL,              -- GrpHdr/MsgId
  stmt_id        text NOT NULL,              -- Stmt/Id
  account_id     text NOT NULL,              -- Stmt/Acct/Id/Othr/Id (synthetic; no IBANs, D-004)
  currency       char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  period_from    date NOT NULL,              -- Stmt/FrToDt/FrDtTm, date as stated (D-026)
  period_to      date NOT NULL,              -- Stmt/FrToDt/ToDtTm
  opening_minor  bigint NOT NULL,            -- Bal OPBD, signed by CdtDbtInd
  closing_minor  bigint NOT NULL,            -- Bal CLBD, signed by CdtDbtInd
  FOREIGN KEY (import_file_id, file_kind, parse_status)
    REFERENCES import_parse (import_file_id, kind, status),
  CHECK (period_to >= period_from)
);

CREATE TABLE bank_entry (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  import_file_id   bigint NOT NULL REFERENCES bank_statement (import_file_id),
  entry_index      integer NOT NULL CHECK (entry_index >= 1),   -- Ntry position, document order
  amount_minor     bigint NOT NULL CHECK (amount_minor <> 0),   -- signed: CRDT > 0, DBIT < 0
  cdt_dbt_ind      text NOT NULL CHECK (cdt_dbt_ind IN ('CRDT', 'DBIT')),
  currency         char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  -- Level 1 reconciles booked entries only; files with PDNG/INFO entries are rejected (D-025).
  status           text NOT NULL CHECK (status = 'BOOK'),
  booking_date     date NOT NULL,
  value_date       date,
  acct_svcr_ref    text,
  ntry_ref         text,
  bank_tx_code     text NOT NULL CHECK (btrim(bank_tx_code) <> ''),
  end_to_end_id    text,
  remittance_ustrd text,
  debtor_name      text,
  -- The stored sign must agree with the credit/debit indicator it was derived from.
  CHECK ((cdt_dbt_ind = 'CRDT') = (amount_minor > 0)),
  UNIQUE (import_file_id, entry_index),
  UNIQUE (id, import_file_id)
);

CREATE TRIGGER ledger_entry_append_only BEFORE UPDATE OR DELETE ON ledger_entry
  FOR EACH ROW EXECUTE FUNCTION reject_modification();
CREATE TRIGGER ledger_entry_no_truncate BEFORE TRUNCATE ON ledger_entry
  FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();
CREATE TRIGGER settlement_line_append_only BEFORE UPDATE OR DELETE ON settlement_line
  FOR EACH ROW EXECUTE FUNCTION reject_modification();
CREATE TRIGGER settlement_line_no_truncate BEFORE TRUNCATE ON settlement_line
  FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();
CREATE TRIGGER bank_statement_append_only BEFORE UPDATE OR DELETE ON bank_statement
  FOR EACH ROW EXECUTE FUNCTION reject_modification();
CREATE TRIGGER bank_statement_no_truncate BEFORE TRUNCATE ON bank_statement
  FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();
CREATE TRIGGER bank_entry_append_only BEFORE UPDATE OR DELETE ON bank_entry
  FOR EACH ROW EXECUTE FUNCTION reject_modification();
CREATE TRIGGER bank_entry_no_truncate BEFORE TRUNCATE ON bank_entry
  FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();
