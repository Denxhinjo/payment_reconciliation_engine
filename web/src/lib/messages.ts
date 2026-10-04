import type { PgError } from "@/lib/db";

/**
 * Database refusals of a resolution, in plain words. The database is the only authority on what
 * a valid resolution is (D-019, D-020, D-067); this only translates which rule fired, by its
 * constraint name or SQLSTATE, mirroring the worker's recon.resolutions. Anything unrecognised
 * returns null, and the caller must not pretend to know what happened.
 */
export function resolutionRefusal(err: PgError): string | null {
  switch (err.constraint) {
    case "resolution_one_root_per_exception":
      return "This exception is already resolved. Correct the resolution in force instead; the original stays on record.";
    case "resolution_superseded_once":
      return "That resolution has already been corrected by someone else. Review the resolution now in force and correct that one.";
    case "resolution_note_check":
      return "A written note of at least 10 characters (not counting surrounding spaces) is required.";
    case "resolution_reason_code_fkey":
      return "Unknown reason code.";
    case "resolution_supersedes_id_exception_id_fkey":
      return "The resolution being corrected does not belong to this exception.";
    case "resolution_exception_id_fkey":
      return "No such exception.";
  }
  if (err.code === "RC004") return "Only exceptions of finished runs can be resolved.";
  return null;
}

export const REQUEUE_OUTCOMES: Record<string, string> = {
  requeued: "Job requeued. Its attempt count is unchanged.",
  already_queued: "Job is already queued; nothing changed.",
  not_failed: "Only failed jobs can be requeued.",
  attempts_exhausted:
    "This job has used its whole attempt budget and cannot be requeued. Requeue never resets attempts (poison-pill protection).",
  not_found: "No such job.",
};
