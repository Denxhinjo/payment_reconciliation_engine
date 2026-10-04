import { authorizeMutation, backTo, parseId } from "@/lib/auth";
import { pool } from "@/lib/db";

/**
 * Request a replay of a finished run. A JOB, not a call (D-078): the request enqueues and returns
 * at once; the worker replays and records the result; the run page shows "pending" until then.
 * Replay passes all three tests for leaving the request path: the work is durable before the
 * response (the job row), the caller needs no answer now, and the scheduled worker does it anyway.
 * A second request while one is pending is a no-op (D-075).
 */
export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const staff = await authorizeMutation(request, "analyst");
  if (staff instanceof Response) return staff;

  const runId = parseId((await params).id);
  if (!runId) return new Response("Not found.", { status: 404 });
  const { rows } = await pool.query<{ status: string }>(
    "SELECT status FROM reconciliation_run WHERE id = $1", [runId]);
  if (!rows[0]) return new Response("Not found.", { status: 404 });
  if (rows[0].status !== "finished") {
    return backTo(request, `/runs/${runId}`, { error: "Only a finished run has a result to replay." });
  }

  const inserted = await pool.query(
    `INSERT INTO job (kind, replay_of_run_id) VALUES ('replay', $1)
     ON CONFLICT (replay_of_run_id) WHERE kind = 'replay' AND status IN ('queued', 'running') DO NOTHING
     RETURNING id`, [runId]);
  return backTo(request, `/runs/${runId}`, {
    notice: inserted.rowCount ? "Replay queued." : "A replay of this run is already pending; nothing changed.",
  });
}
