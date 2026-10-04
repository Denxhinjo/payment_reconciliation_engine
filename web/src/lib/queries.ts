import { pool } from "@/lib/db";

/**
 * Every read the UI makes. Each returns rows as stored; nothing is summed, counted or derived in
 * TypeScript (D-076). A figure the views do not expose is added to a view in a migration.
 * Counts and amounts are bigint, which pg returns as strings; they stay strings.
 */

export interface RunOverview {
  run_id: string;
  engine_version: string;
  engine_git_sha: string | null;
  status: "running" | "finished" | "failed";
  error: string | null;
  started_at: Date;
  finished_at: Date | null;
  result_sha256: string | null;
  ledger_file_id: string;
  ledger_sha256: string;
  settlement_file_id: string;
  settlement_sha256: string;
  bank_file_id: string;
  bank_sha256: string;
  replay_of_run_id: string | null;
  replay_outcome: "identical" | "different" | "failed" | "running" | null;
  original_result_sha256: string | null;
  matches: string;
  exceptions: string;
  matches_exact: string;
  matches_gross_net: string;
  matches_many_to_one: string;
  exceptions_open: string;
}

const RUN_COLUMNS = `run_id::text, engine_version, engine_git_sha, status, error, started_at, finished_at,
  result_sha256, ledger_file_id::text, ledger_sha256, settlement_file_id::text, settlement_sha256,
  bank_file_id::text, bank_sha256, replay_of_run_id::text, replay_outcome, original_result_sha256,
  matches::text, exceptions::text, matches_exact::text, matches_gross_net::text,
  matches_many_to_one::text, exceptions_open::text`;

export async function listRuns(): Promise<RunOverview[]> {
  const { rows } = await pool.query<RunOverview>(
    `SELECT ${RUN_COLUMNS} FROM run_overview ORDER BY run_id DESC LIMIT 200`);
  return rows;
}

export async function getRun(runId: string): Promise<RunOverview | null> {
  const { rows } = await pool.query<RunOverview>(
    `SELECT ${RUN_COLUMNS} FROM run_overview WHERE run_id = $1`, [runId]);
  return rows[0] ?? null;
}

export async function replaysOf(runId: string): Promise<RunOverview[]> {
  const { rows } = await pool.query<RunOverview>(
    `SELECT ${RUN_COLUMNS} FROM run_overview WHERE replay_of_run_id = $1 ORDER BY run_id DESC`, [runId]);
  return rows;
}

export interface JobRow {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "failed";
  attempts: number;
  max_attempts: number;
  error: string | null;
  import_file_id: string | null;
  replay_of_run_id: string | null;
  run_id: string | null;
  created_at: Date;
  finished_at: Date | null;
  requeues: string;
}

const JOB_COLUMNS = `j.id::text, j.kind, j.status, j.attempts, recon_max_job_attempts() AS max_attempts,
  j.error, j.import_file_id::text, j.replay_of_run_id::text, j.run_id::text, j.created_at, j.finished_at,
  (SELECT count(*) FROM job_requeue q WHERE q.job_id = j.id)::text AS requeues`;

/** Replay jobs for a run that have not finished: shown as "replay pending". */
export async function pendingReplays(runId: string): Promise<JobRow[]> {
  const { rows } = await pool.query<JobRow>(
    `SELECT ${JOB_COLUMNS} FROM job j WHERE j.kind = 'replay' AND j.replay_of_run_id = $1
       AND j.status IN ('queued', 'running') ORDER BY j.id`, [runId]);
  return rows;
}

/** Replay jobs for a run that failed or were refused (no replay run was ever recorded). */
export async function failedReplayJobs(runId: string): Promise<JobRow[]> {
  const { rows } = await pool.query<JobRow>(
    `SELECT ${JOB_COLUMNS} FROM job j WHERE j.kind = 'replay' AND j.replay_of_run_id = $1
       AND j.status = 'failed' ORDER BY j.id DESC`, [runId]);
  return rows;
}

export async function listJobs(): Promise<JobRow[]> {
  const { rows } = await pool.query<JobRow>(
    `SELECT ${JOB_COLUMNS} FROM job j ORDER BY j.id DESC LIMIT 200`);
  return rows;
}

export interface RequeueRow {
  job_id: string;
  requeued_by: string;
  requeued_at: Date;
  attempts_at_requeue: number;
  previous_error: string;
}

export async function listRequeues(): Promise<RequeueRow[]> {
  const { rows } = await pool.query<RequeueRow>(
    `SELECT q.job_id::text, s.display_name AS requeued_by, q.requeued_at, q.attempts_at_requeue,
            q.previous_error
       FROM job_requeue q JOIN staff_user s ON s.id = q.requeued_by
      ORDER BY q.id DESC LIMIT 100`);
  return rows;
}

export interface ImportFileRow {
  id: string;
  kind: "ledger" | "settlement" | "bank";
  original_name: string;
  sha256: string;
  byte_size: string;
  uploaded_by: string;
  uploaded_at: Date;
  parse_status: "parsed" | "rejected" | null;
  parse_error: string | null;
  parser_version: string | null;
}

export async function listImportFiles(): Promise<ImportFileRow[]> {
  const { rows } = await pool.query<ImportFileRow>(
    `SELECT f.id::text, f.kind, f.original_name, encode(f.sha256, 'hex') AS sha256,
            octet_length(f.raw)::text AS byte_size, s.display_name AS uploaded_by, f.uploaded_at,
            p.status AS parse_status, p.error AS parse_error, p.parser_version
       FROM import_file f
       JOIN staff_user s ON s.id = f.uploaded_by
       LEFT JOIN import_parse p ON p.import_file_id = f.id
      ORDER BY f.id DESC LIMIT 200`);
  return rows;
}

export interface QueueRow {
  exception_id: string;
  run_id: string;
  ordinal: number;
  suggested_reason: string;
  explanation: string;
  status: "open" | "resolved";
  current_resolution_id: string | null;
  current_reason_code: string | null;
  current_note: string | null;
  resolved_by: string | null;
  resolved_at: Date | null;
  resolution_count: string;
}

const QUEUE_COLUMNS = `exception_id::text, run_id::text, ordinal, suggested_reason, explanation, status,
  current_resolution_id::text, current_reason_code, current_note, resolved_by, resolved_at,
  resolution_count::text`;

export async function exceptionQueue(runId: string, status: string): Promise<QueueRow[]> {
  const { rows } = await pool.query<QueueRow>(
    `SELECT ${QUEUE_COLUMNS} FROM exception_queue
      WHERE run_id = $1 AND ($2 = 'all' OR status = $2) ORDER BY ordinal`, [runId, status]);
  return rows;
}

export async function getException(exceptionId: string): Promise<QueueRow | null> {
  const { rows } = await pool.query<QueueRow>(
    `SELECT ${QUEUE_COLUMNS} FROM exception_queue WHERE exception_id = $1`, [exceptionId]);
  return rows[0] ?? null;
}

export interface EvidenceRow {
  source: "ledger" | "settlement" | "bank";
  identifier: string;
  on_date: string;
  reference: string | null;
  amount_minor: string;
  detail: string;
}

/** The same evidence query as the worker's recon.resolutions.evidence(). */
export async function evidence(exceptionId: string): Promise<EvidenceRow[]> {
  const { rows } = await pool.query<EvidenceRow>(
    `SELECT 'ledger' AS source, le.entry_id AS identifier, le.booked_on::text AS on_date,
            le.payment_reference AS reference, le.amount_minor::text AS amount_minor,
            le.channel || ' ' || le.entry_type AS detail, 1 AS k, le.row_number AS n
       FROM allocation_ledger a JOIN ledger_entry le ON le.id = a.ledger_entry_id
      WHERE a.exception_id = $1
     UNION ALL
     SELECT 'settlement', sl.balance_transaction_id, sl.created_on::text, sl.merchant_reference,
            sl.gross_minor::text, 'fee ' || sl.fee_minor || ', net ' || sl.net_minor || ', payout '
            || sl.payout_id || ' dated ' || sl.payout_date, 2, sl.row_number
       FROM allocation_settlement a JOIN settlement_line sl ON sl.id = a.settlement_line_id
      WHERE a.exception_id = $1
     UNION ALL
     SELECT 'bank', '#' || be.entry_index || ' ' || coalesce(be.acct_svcr_ref, ''),
            be.booking_date::text, coalesce(be.end_to_end_id, be.remittance_ustrd),
            be.amount_minor::text, be.cdt_dbt_ind || ' ' || be.bank_tx_code, 3, be.entry_index
       FROM allocation_bank a JOIN bank_entry be ON be.id = a.bank_entry_id
      WHERE a.exception_id = $1
     ORDER BY k, n`, [exceptionId]);
  return rows;
}

export interface HistoryRow {
  resolution_id: string;
  supersedes_id: string | null;
  reason_code: string;
  reason_label: string;
  note: string;
  resolved_by: string;
  created_at: Date;
  in_force: boolean;
}

/** The resolution chain, oldest first; the last is in force (same query as the worker). */
export async function resolutionHistory(exceptionId: string): Promise<HistoryRow[]> {
  const { rows } = await pool.query<HistoryRow>(
    `WITH RECURSIVE chain AS (
       SELECT r.*, 1 AS depth FROM resolution r WHERE r.exception_id = $1 AND r.supersedes_id IS NULL
       UNION ALL
       SELECT r.*, c.depth + 1 FROM resolution r JOIN chain c ON r.supersedes_id = c.id)
     SELECT c.id::text AS resolution_id, c.supersedes_id::text, c.reason_code, rr.label AS reason_label,
            c.note, s.display_name AS resolved_by, c.created_at,
            NOT EXISTS (SELECT 1 FROM resolution n WHERE n.supersedes_id = c.id) AS in_force
       FROM chain c JOIN resolution_reason rr ON rr.code = c.reason_code
       JOIN staff_user s ON s.id = c.resolved_by
      ORDER BY c.depth`, [exceptionId]);
  return rows;
}

export async function reasonCodes(): Promise<{ code: string; label: string }[]> {
  const { rows } = await pool.query<{ code: string; label: string }>(
    "SELECT code, label FROM resolution_reason ORDER BY code");
  return rows;
}

export interface StaffRow {
  id: string;
  display_name: string;
  role: string;
}

export async function syntheticStaff(): Promise<StaffRow[]> {
  const { rows } = await pool.query<StaffRow>(
    "SELECT id::text, display_name, role FROM staff_user WHERE is_synthetic ORDER BY display_name");
  return rows;
}
