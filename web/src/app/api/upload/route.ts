import { authorizeMutation, backTo } from "@/lib/auth";
import { withTransaction } from "@/lib/db";
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

  const outcome = await withTransaction(async (client): Promise<{ notice: NoticeCode; ref: string }> => {
    const inserted = await client.query<{ id: string }>(
      `INSERT INTO import_file (kind, original_name, raw, uploaded_by) VALUES ($1, $2, $3, $4)
       ON CONFLICT (sha256) DO NOTHING RETURNING id::text`,
      [kind, name, raw, staff.id]);
    if (inserted.rows[0]) {
      await client.query("INSERT INTO job (kind, import_file_id) VALUES ('parse_file', $1)", [inserted.rows[0].id]);
      return { notice: "upload_stored", ref: inserted.rows[0].id };
    }
    const existing = await client.query<{ id: string; kind: string }>(
      "SELECT id::text, kind FROM import_file WHERE sha256 = sha256($1::bytea)", [raw]);
    return { notice: "upload_duplicate", ref: existing.rows[0].id };
  });
  return backTo(request, "/upload", outcome);
}
