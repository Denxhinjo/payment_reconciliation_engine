import { UploadPicker } from "@/components/client";
import { Actor, Flash, flashFrom, LockIcon, QueueStatus, Shell, StatusChip, type SearchParams } from "@/components/shell";
import { requirePageStaff } from "@/lib/auth";
import { formatTimestamp } from "@/lib/format";
import { grouped, KIND_FORMAT, KIND_LABEL, ROW_NOUN } from "@/lib/present";
import { importFileCounts, importFiles, pendingReconciles } from "@/lib/queries";

export const dynamic = "force-dynamic";

/** Upload a file, request a reconciliation, and see every imported file. Parsing and matching
 * happen in the worker; this page only stores and enqueues (D-078). */
export default async function UploadPage({ searchParams }: { searchParams: SearchParams }) {
  const staff = await requirePageStaff();
  const [files, counts, reconciles, flash] = await Promise.all([
    importFiles(), importFileCounts(), pendingReconciles(), flashFrom(searchParams)]);
  const parsed = (kind: string) => files.filter((f) => f.kind === kind && f.parse_status === "parsed");

  return (
    <Shell staff={staff} active="upload">
      <main className="page">
        <div className="page-head">
          <div>
            <h1>Upload and reconcile</h1>
            <p className="lead">Add the month&apos;s three files, then queue a reconciliation. The worker parses and matches in the background.</p>
          </div>
        </div>
        <Flash {...flash} />

        <div className="grid split">
          <section className="card card-pad">
            <div className="step"><span className="step-n">1</span><h2>Upload a file</h2></div>
            <form action="/api/upload" method="post" encType="multipart/form-data">
              <UploadPicker />
            </form>
            <p className="lock-note">
              <LockIcon /> Identical bytes are recognised by SHA-256 and stored once. A file the parser
              rejects is kept as evidence but can never be used in a run.
            </p>
          </section>

          <section className="card card-pad">
            <div className="step"><span className="step-n">2</span><h2>Request a reconciliation</h2></div>
            <form action="/api/runs" method="post">
              {(["ledger", "settlement", "bank"] as const).map((kind) => (
                <label key={kind} className="field">
                  <span>{kind === "bank" ? "Bank statement" : KIND_LABEL[kind]}</span>
                  <select name={`${kind}_file_id`} required defaultValue={parsed(kind)[0]?.file_id ?? ""}>
                    <option value="" disabled>Choose a parsed {kind} file…</option>
                    {parsed(kind).map((f) => (
                      <option key={f.file_id} value={f.file_id}>
                        #{f.file_id} · {f.original_name} · {grouped(f.parsed_rows)} {ROW_NOUN[kind]}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
              <p className="small muted" style={{ marginBottom: "var(--space-4)" }}>Only parsed files are offered.</p>
              <button type="submit" className="button primary block">Queue reconciliation</button>
            </form>
            <div style={{ borderTop: "var(--border-width) solid var(--border)", marginTop: "var(--space-5)", paddingTop: "var(--space-4)" }}>
              <h3 className="eyebrow">Waiting for the worker</h3>
              {reconciles.length === 0 ? <p className="muted" style={{ marginTop: "var(--space-2)" }}>Nothing queued.</p> : (
                <ul className="rows" style={{ margin: "var(--space-2) calc(-1 * var(--space-5)) 0" }}>
                  {reconciles.map((job) => (
                    <li key={job.id} className="row-line">
                      <span className="grow">
                        <span className="mono strong">Job #{job.id}</span> <span className="mono small muted">files {job.files}</span><br />
                        <QueueStatus status={job.status} overdue={job.overdue} />
                      </span>
                      <StatusChip status={job.status} />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        </div>

        <section className="card" style={{ marginTop: "var(--space-4)" }}>
          <div className="card-head">
            <h2>Imported files</h2>
            <span className="hint">
              {counts.files} files · {counts.parsed === counts.files ? "all parsed"
                : `${counts.parsed} parsed, ${counts.rejected} rejected, ${counts.awaiting_parse} awaiting parse`}
            </span>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>File</th><th>Name</th><th>Kind</th><th>Format</th><th>Status</th><th className="num">Rows</th><th>Uploaded by</th><th>Uploaded (UTC)</th></tr>
              </thead>
              <tbody>
                {files.map((f) => (
                  <tr key={f.file_id}>
                    <td className="id strong" style={{ color: "var(--ink)" }}>#{f.file_id}</td>
                    <td className="mono small" title={`SHA-256 ${f.sha256} · ${f.byte_size} bytes`}>{f.original_name}</td>
                    <td>{KIND_LABEL[f.kind]}</td>
                    <td className="muted">{f.parse_status === "parsed" ? KIND_FORMAT[f.kind] : f.kind === "bank" ? "camt.053 XML" : "CSV"}</td>
                    <td>
                      <StatusChip status={f.parse_status ?? f.parse_job_status ?? "queued"} />
                      {f.parse_status === null && f.parse_job_status ? (
                        <div className="small"><QueueStatus status={f.parse_job_status} overdue={f.parse_job_overdue ?? false} /></div>
                      ) : null}
                      {f.parse_error ? <div className="small bad-text">{f.parse_error}</div> : null}
                    </td>
                    <td className="num">{f.parsed_rows ? grouped(f.parsed_rows) : <span className="dim">—</span>}</td>
                    <td><Actor name={f.uploaded_by} role={f.uploaded_by_role} /></td>
                    <td className="mono small">{formatTimestamp(f.uploaded_at).replace(" UTC", "").slice(0, 16)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </Shell>
  );
}
