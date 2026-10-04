import { authorizeMutation, backTo, parseId } from "@/lib/auth";
import { pool } from "@/lib/db";

/**
 * Request a reconciliation of three parsed files. Enqueues a reconcile job and returns; the
 * worker computes the run (D-078). A second request for the same three files while one is
 * queued or running is a no-op (unique partial index, D-075).
 */
export async function POST(request: Request) {
  const staff = await authorizeMutation(request, "analyst");
  if (staff instanceof Response) return staff;

  let ids: Record<string, string | null>;
  try {
    const form = await request.formData();
    ids = {
      ledger: parseId(form.get("ledger_file_id")),
      settlement: parseId(form.get("settlement_file_id")),
      bank: parseId(form.get("bank_file_id")),
    };
  } catch {
    return backTo(request, "/upload", { error: "Choose one file of each kind." });
  }
  if (!ids.ledger || !ids.settlement || !ids.bank) {
    return backTo(request, "/upload", { error: "Choose one file of each kind." });
  }

  // Each slot must hold a successfully parsed file of the right kind. The database enforces this
  // again when the worker creates the run (D-032); checking here lets the request fail clearly.
  const { rows } = await pool.query<{ kind: string }>(
    `SELECT p.kind FROM import_parse p WHERE p.status = 'parsed' AND
       ((p.import_file_id = $1 AND p.kind = 'ledger') OR (p.import_file_id = $2 AND p.kind = 'settlement')
        OR (p.import_file_id = $3 AND p.kind = 'bank'))`,
    [ids.ledger, ids.settlement, ids.bank]);
  if (rows.length !== 3) {
    return backTo(request, "/upload", { error: "Each slot needs a successfully parsed file of that kind." });
  }

  const inserted = await pool.query(
    `INSERT INTO job (kind, ledger_file_id, settlement_file_id, bank_file_id) VALUES ('reconcile', $1, $2, $3)
     ON CONFLICT (ledger_file_id, settlement_file_id, bank_file_id)
       WHERE kind = 'reconcile' AND status IN ('queued', 'running') DO NOTHING
     RETURNING id`,
    [ids.ledger, ids.settlement, ids.bank]);
  return backTo(request, "/upload", {
    notice: inserted.rowCount
      ? "Reconciliation queued. It appears under Runs when the worker has computed it."
      : "A reconciliation of these three files is already queued; nothing changed.",
  });
}
