import type { PgError } from "@/lib/db";

/**
 * Every message the UI can show after an action, as a fixed table (D-082, closes F18).
 *
 * A POST redirects back with a CODE (?notice=… or ?error=…) and at most a numeric reference
 * (?ref=…). The page looks the code up here. Free text from a URL is never rendered: an unknown
 * code shows nothing, and a template's {ref} is filled only with a positive integer. So nobody
 * can craft a link that makes this site display words it never wrote.
 */

const NOTICES = {
  upload_stored: "Stored as file #{ref}; queued for parsing.",
  upload_duplicate: "These exact bytes were already imported as file #{ref}; nothing done.",
  reconcile_queued: "Reconciliation queued. It appears under Runs when the worker has computed it.",
  reconcile_already_queued: "A reconciliation of these three files is already queued; nothing changed.",
  replay_queued: "Replay queued.",
  replay_already_pending: "A replay of this run is already pending; nothing changed.",
  resolution_recorded: "Resolution #{ref} recorded.",
  correction_recorded: "Correction recorded as resolution #{ref}.",
  job_requeued: "Job #{ref} requeued. Its attempt count is unchanged.",
  job_already_queued: "Job #{ref} is already queued; nothing changed.",
} as const;

const ERRORS = {
  signin_choose: "Choose a staff account.",
  signin_unknown: "Unknown staff account.",
  upload_incomplete: "Choose a file and its kind.",
  upload_kind: "Unknown file kind.",
  upload_empty: "The file is empty.",
  upload_too_large: "The file is larger than 4 MiB.",
  reconcile_choose: "Choose one file of each kind.",
  reconcile_slots: "Each slot needs a successfully parsed file of that kind.",
  replay_not_finished: "Only a finished run has a result to replay.",
  form_unreadable: "The form could not be read.",
  correction_needs_target: "A correction must name the resolution it supersedes.",
  already_resolved:
    "This exception is already resolved. Correct the resolution in force instead; the original stays on record.",
  stale_correction:
    "That resolution has already been corrected by someone else. Review the resolution now in force and correct that one.",
  note_too_short: "A written note of at least 10 characters (not counting surrounding spaces) is required.",
  unknown_reason: "Unknown reason code.",
  wrong_exception: "The resolution being corrected does not belong to this exception.",
  run_not_finished: "Only exceptions of finished runs can be resolved.",
  job_not_failed: "Job #{ref}: only failed jobs can be requeued.",
  job_attempts_exhausted:
    "Job #{ref} has used its whole attempt budget and cannot be requeued. Requeue never resets attempts (poison-pill protection).",
} as const;

export type NoticeCode = keyof typeof NOTICES;
export type ErrorCode = keyof typeof ERRORS;

const REF = /^[1-9][0-9]{0,17}$/;

/** The fixed message for a code, or null. Unknown codes, prototype keys and bad refs give null. */
export function flashMessage(kind: "notice" | "error", code: string | undefined, ref: string | undefined): string | null {
  if (!code) return null;
  const table: Record<string, string> = kind === "notice" ? NOTICES : ERRORS;
  if (!Object.prototype.hasOwnProperty.call(table, code)) return null; // not "constructor", "__proto__", …
  const template = table[code];
  if (!template.includes("{ref}")) return template;
  return ref && REF.test(ref) ? template.replace("{ref}", ref) : null;
}

/**
 * Database refusals of a resolution, as error codes. The database is the only authority on what a
 * valid resolution is (D-019, D-020, D-067); this only says which rule fired, by constraint name or
 * SQLSTATE, mirroring the worker's recon.resolutions. Unrecognised: null, and the caller must not
 * pretend to know what happened.
 */
export function resolutionRefusal(err: PgError): ErrorCode | "no_such_exception" | null {
  switch (err.constraint) {
    case "resolution_one_root_per_exception": return "already_resolved";
    case "resolution_superseded_once": return "stale_correction";
    case "resolution_note_check": return "note_too_short";
    case "resolution_reason_code_fkey": return "unknown_reason";
    case "resolution_supersedes_id_exception_id_fkey": return "wrong_exception";
    case "resolution_exception_id_fkey": return "no_such_exception";
  }
  if (err.code === "RC004") return "run_not_finished";
  return null;
}

/** requeue_failed_job() outcomes as codes (D-074). */
export const REQUEUE_CODES: Record<string, { notice?: NoticeCode; error?: ErrorCode }> = {
  requeued: { notice: "job_requeued" },
  already_queued: { notice: "job_already_queued" },
  not_failed: { error: "job_not_failed" },
  attempts_exhausted: { error: "job_attempts_exhausted" },
};
