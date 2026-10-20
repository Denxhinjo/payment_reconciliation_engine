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
  requested_by_name: string | null;   // requester of the job that made the run (D-091)
  requested_by_role: string | null;
}

const RUN_COLUMNS = `run_id::text, engine_version, engine_git_sha, status, error, started_at, finished_at,
  result_sha256, ledger_file_id::text, ledger_sha256, settlement_file_id::text, settlement_sha256,
  bank_file_id::text, bank_sha256, replay_of_run_id::text, replay_outcome, original_result_sha256,
  matches::text, exceptions::text, matches_exact::text, matches_gross_net::text,
  matches_many_to_one::text, exceptions_open::text, requested_by_name, requested_by_role`;

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
  overdue: boolean;            // decided by the database (job_overview, D-088)
  requested_by_name: string;   // who asked for the job (D-091)
  requested_by_role: string;
}

const JOB_COLUMNS = `j.id::text, j.kind, j.status, j.attempts, j.max_attempts, j.error,
  j.import_file_id::text, j.replay_of_run_id::text, j.run_id::text, j.created_at, j.finished_at,
  j.requeues::text, j.overdue, j.requested_by_name, j.requested_by_role`;

/** Replay jobs for a run that have not finished: shown as "replay pending". */
export async function pendingReplays(runId: string): Promise<JobRow[]> {
  const { rows } = await pool.query<JobRow>(
    `SELECT ${JOB_COLUMNS} FROM job_overview j WHERE j.kind = 'replay' AND j.replay_of_run_id = $1
       AND j.status IN ('queued', 'running') ORDER BY j.id`, [runId]);
  return rows;
}

/** Replay jobs for a run that failed or were refused (no replay run was ever recorded). */
export async function failedReplayJobs(runId: string): Promise<JobRow[]> {
  const { rows } = await pool.query<JobRow>(
    `SELECT ${JOB_COLUMNS} FROM job_overview j WHERE j.kind = 'replay' AND j.replay_of_run_id = $1
       AND j.status = 'failed' ORDER BY j.id DESC`, [runId]);
  return rows;
}

/** Requested reconciliations not yet computed: shown on the upload page with their status. */
export async function pendingReconciles(): Promise<(JobRow & { files: string })[]> {
  const { rows } = await pool.query<JobRow & { files: string }>(
    `SELECT ${JOB_COLUMNS}, '#' || j.ledger_file_id || ', #' || j.settlement_file_id || ', #' || j.bank_file_id AS files
       FROM job_overview j WHERE j.kind = 'reconcile' AND j.status IN ('queued', 'running') ORDER BY j.id`);
  return rows;
}

export async function listJobs(): Promise<JobRow[]> {
  const { rows } = await pool.query<JobRow>(
    `SELECT ${JOB_COLUMNS} FROM job_overview j ORDER BY j.id DESC LIMIT 200`);
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
  uploaded_by_role: string;
  uploaded_at: Date;
  parse_status: "parsed" | "rejected" | null;
  parse_error: string | null;
  parser_version: string | null;
  parse_job_status: string | null;
  parse_job_overdue: boolean | null;
}

export async function listImportFiles(): Promise<ImportFileRow[]> {
  const { rows } = await pool.query<ImportFileRow>(
    `SELECT f.id::text, f.kind, f.original_name, encode(f.sha256, 'hex') AS sha256,
            octet_length(f.raw)::text AS byte_size, s.display_name AS uploaded_by, s.role AS uploaded_by_role,
            f.uploaded_at,
            p.status AS parse_status, p.error AS parse_error, p.parser_version,
            pj.status AS parse_job_status, pj.overdue AS parse_job_overdue
       FROM import_file f
       JOIN staff_user s ON s.id = f.uploaded_by
       LEFT JOIN import_parse p ON p.import_file_id = f.id
       LEFT JOIN job_overview pj ON pj.import_file_id = f.id AND pj.kind = 'parse_file'
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

/** The accounts offered on the sign-in page. Never the system actor, which cannot sign in. */
export async function syntheticStaff(): Promise<StaffRow[]> {
  const { rows } = await pool.query<StaffRow>(
    "SELECT id::text, display_name, role FROM staff_user WHERE is_synthetic AND role <> 'system' ORDER BY display_name");
  return rows;
}

// --- Redesign (D-096): every figure below is read from a view added in migration 0014 ------------

export async function latestRunId(): Promise<string | null> {
  const { rows } = await pool.query<{ run_id: string | null }>("SELECT run_id::text FROM latest_run");
  return rows[0]?.run_id ?? null;
}

export interface RunFigures {
  run_id: string;
  period_from: string | null;
  period_to: string | null;
  period_label: string | null;
  currency: string | null;
  ledger_name: string;
  settlement_name: string;
  bank_name: string;
  ledger_rows: string;
  settlement_rows: string;
  settlement_payouts: string;
  bank_entries: string;
  rows_compared: string;
  open_needing_review: string;
  open_timing: string;
  resolved: string;
  open_value_under_review_minor: string;
  latest_replay_run_id: string | null;
  latest_replay_outcome: string | null;
}

export async function runFigures(runId: string): Promise<RunFigures | null> {
  const { rows } = await pool.query<RunFigures>(
    `SELECT run_id::text, period_from::text, period_to::text, period_label, currency, ledger_name,
            settlement_name, bank_name, ledger_rows::text, settlement_rows::text, settlement_payouts::text,
            bank_entries::text, rows_compared::text, open_needing_review::text, open_timing::text,
            resolved::text, open_value_under_review_minor::text, latest_replay_run_id::text,
            latest_replay_outcome
       FROM run_figures WHERE run_id = $1`, [runId]);
  return rows[0] ?? null;
}

/** Open exceptions of the run the dashboard opens on: the count in the navigation badge. */
export async function openBadge(): Promise<string | null> {
  const { rows } = await pool.query<{ open: string }>(
    "SELECT o.exceptions_open::text AS open FROM latest_run l JOIN run_overview o ON o.run_id = l.run_id");
  return rows[0]?.open ?? null;
}

export interface ExceptionFigures {
  exception_id: string;
  run_id: string;
  ordinal: number;
  suggested_reason: string;
  status: "open" | "resolved";
  needs_review: boolean;
  ledger_rows: string;
  settlement_rows: string;
  bank_rows: string;
  ledger_minor: string | null;
  settlement_gross_minor: string | null;
  settlement_fee_minor: string | null;
  settlement_net_minor: string | null;
  bank_minor: string | null;
  expected_minor: string | null;
  received_minor: string | null;
  shortfall_minor: string | null;
  amount_at_issue_minor: string | null;
  reference: string | null;
  payouts: string;
  other_open_in_run: string;
}

const FIGURE_COLUMNS = `f.exception_id::text, f.run_id::text, f.ordinal, f.suggested_reason, f.status, f.needs_review,
  f.ledger_rows::text, f.settlement_rows::text, f.bank_rows::text, f.ledger_minor::text,
  f.settlement_gross_minor::text, f.settlement_fee_minor::text, f.settlement_net_minor::text,
  f.bank_minor::text, f.expected_minor::text, f.received_minor::text, f.shortfall_minor::text,
  f.amount_at_issue_minor::text, f.reference, f.payouts::text, f.other_open_in_run::text`;

export async function exceptionFigures(exceptionId: string): Promise<ExceptionFigures | null> {
  const { rows } = await pool.query<ExceptionFigures>(
    `SELECT ${FIGURE_COLUMNS} FROM exception_figures f WHERE f.exception_id = $1`, [exceptionId]);
  return rows[0] ?? null;
}

export type QueueFilter = "open" | "review" | "timing" | "resolved" | "all";

export type QueueItem = ExceptionFigures & {
  explanation: string;
  resolved_by: string | null;
  current_reason_code: string | null;
};

/** The queue: needing-review items first and timing last, each group in the engine's order. */
export async function queueWithFigures(runId: string, filter: QueueFilter): Promise<QueueItem[]> {
  const { rows } = await pool.query<QueueItem>(
    `SELECT ${FIGURE_COLUMNS}, q.explanation, q.resolved_by, q.current_reason_code
       FROM exception_queue q JOIN exception_figures f ON f.exception_id = q.exception_id
      WHERE q.run_id = $1 AND (
            $2 = 'all'
         OR ($2 = 'open' AND q.status = 'open')
         OR ($2 = 'review' AND q.status = 'open' AND f.needs_review)
         OR ($2 = 'timing' AND q.status = 'open' AND NOT f.needs_review)
         OR ($2 = 'resolved' AND q.status = 'resolved'))
      ORDER BY f.needs_review DESC, q.ordinal`, [runId, filter]);
  return rows;
}

export async function exceptionReasons(runId: string): Promise<{ suggested_reason: string; exceptions: string }[]> {
  const { rows } = await pool.query<{ suggested_reason: string; exceptions: string }>(
    `SELECT suggested_reason, exceptions::text FROM run_exception_reasons WHERE run_id = $1
      ORDER BY suggested_reason = 'timing', first_ordinal`, [runId]);
  return rows;
}

export interface PlantedItem {
  item_id: string;
  category: "planted" | "timing";
  title: string;
  description: string;
  expected_kind: "exception" | "match";
  expected_outcome: string;
  caught: boolean;
  exception_id: string | null;
  outcome_ordinal: number | null;
}

export async function plantedItems(runId: string): Promise<PlantedItem[]> {
  const { rows } = await pool.query<PlantedItem>(
    `SELECT item_id, category, title, description, expected_kind, expected_outcome, caught,
            exception_id::text, outcome_ordinal
       FROM run_planted_items WHERE run_id = $1 ORDER BY item_id`, [runId]);
  return rows;
}

export async function plantedCaught(runId: string): Promise<{ caught: string; total: string } | null> {
  const { rows } = await pool.query<{ caught: string; total: string }>(
    `SELECT count(*) FILTER (WHERE caught)::text AS caught, count(*)::text AS total
       FROM run_planted_items WHERE run_id = $1 AND category = 'planted' HAVING count(*) > 0`, [runId]);
  return rows[0] ?? null;
}

export interface FileRow {
  file_id: string;
  kind: "ledger" | "settlement" | "bank";
  original_name: string;
  sha256: string;
  byte_size: string;
  uploaded_by: string;
  uploaded_by_role: string;
  uploaded_at: Date;
  parse_status: "parsed" | "rejected" | null;
  parse_error: string | null;
  parse_job_status: string | null;
  parse_job_overdue: boolean | null;
  parsed_rows: string | null;
}

export async function importFiles(): Promise<FileRow[]> {
  const { rows } = await pool.query<FileRow>(
    `SELECT file_id::text, kind, original_name, sha256, byte_size::text, uploaded_by, uploaded_by_role,
            uploaded_at, parse_status, parse_error, parse_job_status, parse_job_overdue, parsed_rows::text
       FROM import_file_overview ORDER BY file_id DESC LIMIT 200`);
  return rows;
}

export interface FileCounts { files: string; parsed: string; rejected: string; awaiting_parse: string }

export async function importFileCounts(): Promise<FileCounts> {
  const { rows } = await pool.query<FileCounts>(
    "SELECT files::text, parsed::text, rejected::text, awaiting_parse::text FROM import_file_counts");
  return rows[0];
}

export interface JobListRow extends JobRow {
  ledger_file_id: string | null;
  settlement_file_id: string | null;
  bank_file_id: string | null;
  queued_at: Date;
  file_kind: string | null;
  file_parse_status: string | null;
  file_rows: string | null;
  run_status: string | null;
  run_matches: string | null;
  run_exceptions: string | null;
  run_replay_outcome: string | null;
}

export async function listJobsWithResults(): Promise<JobListRow[]> {
  const { rows } = await pool.query<JobListRow>(
    `SELECT ${JOB_COLUMNS}, j.ledger_file_id::text, j.settlement_file_id::text, j.bank_file_id::text,
            j.queued_at, r.file_kind, r.file_parse_status, r.file_rows::text, r.run_status,
            r.run_matches::text, r.run_exceptions::text, r.run_replay_outcome
       FROM job_overview j JOIN job_results r ON r.job_id = j.id
      ORDER BY j.id DESC LIMIT 200`);
  return rows;
}

export interface JobCounts { total: string; queued: string; running: string; failed: string; done: string }

export async function jobCounts(): Promise<JobCounts> {
  const { rows } = await pool.query<JobCounts>(
    "SELECT total::text, queued::text, running::text, failed::text, done::text FROM job_counts");
  return rows[0];
}
