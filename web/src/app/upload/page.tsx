import { Flash, flashFrom, QueueStatus, Shell, StatusChip, type SearchParams } from "@/components/shell";
import { requirePageStaff } from "@/lib/auth";
import { formatTimestamp, shortHash } from "@/lib/format";
import { listImportFiles, pendingReconciles } from "@/lib/queries";

export const dynamic = "force-dynamic";

export default async function UploadPage({ searchParams }: { searchParams: SearchParams }) {
  const staff = await requirePageStaff();
  const [files, reconciles, flash] = await Promise.all([
    listImportFiles(), pendingReconciles(), flashFrom(searchParams)]);
  const parsed = (kind: string) => files.filter((f) => f.kind === kind && f.parse_status === "parsed");

  return (
    <Shell staff={staff}>
      <main className="page">
        <h1>Upload and reconcile</h1>
        <Flash {...flash} />

        <h2>Upload a file</h2>
        <form action="/api/upload" method="post" encType="multipart/form-data" className="panel stack">
          <label>
            Kind
            <select name="kind" required defaultValue="">
              <option value="" disabled>Choose…</option>
              <option value="ledger">Internal ledger (CSV)</option>
              <option value="settlement">Orrery settlement report (CSV)</option>
              <option value="bank">Bank statement (camt.053.001.02 XML)</option>
            </select>
          </label>
          <label>File <input type="file" name="file" required /></label>
          <button type="submit" className="button">Upload</button>
          <p className="dim small">
            Identical bytes are recognised by SHA-256 and stored once. Files are parsed by the worker.
          </p>
        </form>

        <h2>Request a reconciliation</h2>
        <form action="/api/runs" method="post" className="panel stack">
          {(["ledger", "settlement", "bank"] as const).map((kind) => (
            <label key={kind}>
              {kind}
              <select name={`${kind}_file_id`} required defaultValue="">
                <option value="" disabled>Choose a parsed {kind} file…</option>
                {parsed(kind).map((f) => (
                  <option key={f.id} value={f.id}>#{f.id} {f.original_name}</option>
                ))}
              </select>
            </label>
          ))}
          <button type="submit" className="button">Queue reconciliation</button>
        </form>

        <h2>Requested reconciliations</h2>
        {reconciles.length === 0 ? <p className="dim">None waiting.</p> : (
          <table className="table">
            <thead><tr><th>Job</th><th>Files</th><th>Status</th><th>When it runs</th></tr></thead>
            <tbody>
              {reconciles.map((job) => (
                <tr key={job.id}>
                  <td className="mono">#{job.id}</td>
                  <td className="mono">{job.files}</td>
                  <td><StatusChip status={job.status} /></td>
                  <td className="small"><QueueStatus status={job.status} overdue={job.overdue} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <h2>Imported files</h2>
        <table className="table">
          <thead>
            <tr><th>File</th><th>Kind</th><th>Name</th><th className="num">Bytes</th><th>SHA-256</th><th>Parse</th><th>Uploaded</th></tr>
          </thead>
          <tbody>
            {files.map((f) => (
              <tr key={f.id}>
                <td className="mono">#{f.id}</td>
                <td>{f.kind}</td>
                <td>{f.original_name}</td>
                <td className="num">{f.byte_size}</td>
                <td className="mono">{shortHash(f.sha256)}</td>
                <td>
                  <StatusChip status={f.parse_status ?? f.parse_job_status ?? "queued"} />
                  {f.parse_status === null && f.parse_job_status ? (
                    <div className="small"><QueueStatus status={f.parse_job_status} overdue={f.parse_job_overdue ?? false} /></div>
                  ) : null}
                  {f.parse_error ? <div className="small bad-text">{f.parse_error}</div> : null}
                </td>
                <td className="mono small">{f.uploaded_by}, {formatTimestamp(f.uploaded_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </main>
    </Shell>
  );
}
