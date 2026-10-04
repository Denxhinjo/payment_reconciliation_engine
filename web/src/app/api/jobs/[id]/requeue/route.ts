import { authorizeMutation, backTo, parseId } from "@/lib/auth";
import { pool } from "@/lib/db";
import { REQUEUE_OUTCOMES } from "@/lib/messages";

/**
 * Requeue a failed job. Controller only, checked here AND inside the database function
 * (requeue_failed_job raises RC005 for anyone else). The function is idempotent (a double click
 * gives one requeue) and never resets attempts, so a poison pill stays capped (D-074). A fresh
 * attempt budget, if ever needed, would be a separate, explicitly named action.
 */
export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const staff = await authorizeMutation(request, "controller");
  if (staff instanceof Response) return staff;

  const jobId = parseId((await params).id);
  if (!jobId) return new Response("Not found.", { status: 404 });
  const { rows } = await pool.query<{ outcome: string }>(
    "SELECT requeue_failed_job($1, $2) AS outcome", [jobId, staff.id]);
  const outcome = rows[0].outcome;
  if (outcome === "not_found") return new Response("Not found.", { status: 404 });
  const message = REQUEUE_OUTCOMES[outcome] ?? outcome;
  return backTo(request, "/jobs", outcome === "requeued" || outcome === "already_queued"
    ? { notice: `Job #${jobId}: ${message}` }
    : { error: `Job #${jobId}: ${message}` });
}
