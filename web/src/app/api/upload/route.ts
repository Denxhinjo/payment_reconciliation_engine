import { authorizeMutation, backTo } from "@/lib/auth";
import { pool } from "@/lib/db";
import type { NoticeCode } from "@/lib/messages";

const KINDS = new Set(["ledger", "settlement", "bank"]);
const MAX_BYTES = 4 * 1024 * 1024; // the database enforces the same limit on import_file.raw

/**
 * Upload one input file. Idempotent by the database-computed SHA-256 (D-005): the same bytes a
 * second time create nothing, not even a parse job. The bytes are stored exactly as received,
 * never decoded or re-encoded. Parsing happens in the worker (a queued parse job).
 */
export async function POST(request: Request) {
  const staff = await authorizeMutation(request, "analyst");
  if (staff instanceof Response) return staff;

  let kind: string, name: string, raw: Buffer;
  try {
    const form = await request.formData();
    kind = String(form.get("kind") ?? "");
    const file = form.get("file");
    if (!(file instanceof File)) throw new Error("no file");
    name = file.name || "upload";
    raw = Buffer.from(await file.arrayBuffer());
  } catch {
    return backTo(request, "/upload", { error: "upload_incomplete" });
  }
  if (!KINDS.has(kind)) return backTo(request, "/upload", { error: "upload_kind" });
  if (raw.length === 0) return backTo(request, "/upload", { error: "upload_empty" });
  if (raw.length > MAX_BYTES) return backTo(request, "/upload", { error: "upload_too_large" });

  // upload_file() stores the bytes and queues the parse job, requested by this staff user, in one
  // statement. The worker's seed command calls the same function (D-091).
  const { rows } = await pool.query<{ id: string; created: boolean }>(
    "SELECT stored_file_id::text AS id, created FROM upload_file($1::file_kind, $2, $3, $4)",
    [kind, name, raw, staff.id]);
  const notice: NoticeCode = rows[0].created ? "upload_stored" : "upload_duplicate";
  return backTo(request, "/upload", { notice, ref: rows[0].id });
}
