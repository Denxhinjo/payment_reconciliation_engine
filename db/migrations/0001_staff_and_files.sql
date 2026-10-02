-- 0001: staff users, raw file imports, parse outcomes.
-- See docs/design.md §4.1 and docs/decisions.md D-005, D-006.

-- Shared trigger function: any UPDATE, DELETE or TRUNCATE on an append-only table is an error.
-- SQLSTATE RC001 lets tests and callers tell this apart from other failures.
CREATE FUNCTION reject_modification() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% on % is not allowed: this table is append-only', TG_OP, TG_TABLE_NAME
    USING ERRCODE = 'RC001';
END;
$$;

-- Staff who upload files and resolve exceptions. In the demo every user is synthetic
-- (D-031). Append-only, because resolutions name the user who made them.
CREATE TABLE staff_user (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  display_name text NOT NULL UNIQUE CHECK (btrim(display_name) <> ''),
  is_synthetic boolean NOT NULL DEFAULT true
);

CREATE TRIGGER staff_user_append_only BEFORE UPDATE OR DELETE ON staff_user
  FOR EACH ROW EXECUTE FUNCTION reject_modification();
CREATE TRIGGER staff_user_no_truncate BEFORE TRUNCATE ON staff_user
  FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();

CREATE TYPE file_kind AS ENUM ('ledger', 'settlement', 'bank');

CREATE TABLE import_file (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind          file_kind NOT NULL,
  original_name text NOT NULL CHECK (btrim(original_name) <> ''),
  -- Raw bytes exactly as uploaded. 1 byte .. 4 MiB: an empty file is never valid, and the
  -- upper bound stays under the web platform's request body limit.
  raw           bytea NOT NULL CHECK (octet_length(raw) BETWEEN 1 AND 4194304),
  -- Derived by the database, so the hash cannot disagree with the bytes (D-005).
  sha256        bytea GENERATED ALWAYS AS (sha256(raw)) STORED,
  uploaded_by   bigint NOT NULL REFERENCES staff_user(id),
  uploaded_at   timestamptz NOT NULL DEFAULT now(),
  -- Idempotent import: the same bytes can only ever be imported once.
  CONSTRAINT import_file_sha256_unique UNIQUE (sha256),
  -- Target for composite foreign keys that pin a reference to a file of the right kind.
  CONSTRAINT import_file_id_kind_unique UNIQUE (id, kind)
);

-- Raw files are evidence: never edited, never removed (D-006).
CREATE TRIGGER import_file_append_only BEFORE UPDATE OR DELETE ON import_file
  FOR EACH ROW EXECUTE FUNCTION reject_modification();
CREATE TRIGGER import_file_no_truncate BEFORE TRUNCATE ON import_file
  FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();

-- The parse outcome lives in its own table so that import_file never needs an UPDATE.
-- One outcome per file, append-only.
CREATE TABLE import_parse (
  import_file_id bigint PRIMARY KEY,
  kind           file_kind NOT NULL,
  parser_version text NOT NULL CHECK (parser_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
  status         text NOT NULL CHECK (status IN ('parsed', 'rejected')),
  error          text,
  parsed_at      timestamptz NOT NULL DEFAULT now(),
  -- The kind must be the file's kind; it is repeated here so that row tables can reference
  -- (file, kind, status) in a single foreign key.
  FOREIGN KEY (import_file_id, kind) REFERENCES import_file (id, kind),
  -- A rejection always says why; a successful parse never carries an error.
  CHECK ((status = 'rejected') = (error IS NOT NULL)),
  -- Target for foreign keys from parsed rows and runs: only a file whose parse outcome is
  -- 'parsed' can have rows or be used in a run.
  CONSTRAINT import_parse_file_kind_status_unique UNIQUE (import_file_id, kind, status)
);

CREATE TRIGGER import_parse_append_only BEFORE UPDATE OR DELETE ON import_parse
  FOR EACH ROW EXECUTE FUNCTION reject_modification();
CREATE TRIGGER import_parse_no_truncate BEFORE TRUNCATE ON import_parse
  FOR EACH STATEMENT EXECUTE FUNCTION reject_modification();
