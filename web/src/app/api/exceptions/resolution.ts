import { authorizeMutation, backTo, parseId } from "@/lib/auth";
import { asPgError, pool } from "@/lib/db";
import { resolutionRefusal } from "@/lib/messages";

/**
 * Resolve or correct an exception: one INSERT into the append-only resolution table. Nothing is
 * pre-validated here; the database refuses invalid resolutions and the refusal is translated by
 * constraint name (D-067). resolved_by is the signed-in staff member, never a form field.
 */
export async function recordResolution(request: Request, rawId: string, correcting: boolean) {
  const staff = await authorizeMutation(request, "analyst");
  if (staff instanceof Response) return staff;

  const exceptionId = parseId(rawId);
  if (!exceptionId) return new Response("Not found.", { status: 404 });
  const page = `/exceptions/${exceptionId}`;

  let reason = "", note = "", supersedes: string | null = null;
  try {
    const form = await request.formData();
    reason = String(form.get("reason_code") ?? "");
    note = String(form.get("note") ?? "");
    supersedes = parseId(form.get("supersedes_id"));
  } catch {
    return backTo(request, page, { error: "form_unreadable" });
  }
  if (correcting && !supersedes) {
    return backTo(request, page, { error: "correction_needs_target" });
  }

  try {
    const { rows } = await pool.query<{ id: string }>(
      `INSERT INTO resolution (exception_id, supersedes_id, reason_code, note, resolved_by)
       VALUES ($1, $2, $3, $4, $5) RETURNING id::text`,
      [exceptionId, correcting ? supersedes : null, reason, note, staff.id]);
    return backTo(request, page, {
      notice: correcting ? "correction_recorded" : "resolution_recorded", ref: rows[0].id,
    });
  } catch (err) {
    const pgError = asPgError(err);
    const code = pgError ? resolutionRefusal(pgError) : null;
    if (code === "no_such_exception") return new Response("Not found.", { status: 404 });
    if (code) return backTo(request, page, { error: code });
    throw err; // unrecognised: do not disguise it as a known refusal
  }
}
