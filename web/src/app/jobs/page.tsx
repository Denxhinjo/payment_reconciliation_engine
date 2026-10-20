import Link from "next/link";
import { Actor, Flash, flashFrom, InfoIcon, QueueStatus, Shell, StatusChip, type SearchParams } from "@/components/shell";
import { requirePageStaff } from "@/lib/auth";
import { formatTimestamp } from "@/lib/format";
import { grouped, ROW_NOUN } from "@/lib/present";
import { jobCounts, listJobsWithResults, listRequeues, type JobListRow } from "@/lib/queries";

export const dynamic = "force-dynamic";

const KIND: Record<string, string> = { parse_file: "Parse", reconcile: "Reconcile", replay: "Replay" };

function Target({ job }: { job: JobListRow }) {
  if (job.kind === "parse_file") return <>File #{job.import_file_id} · {job.file_kind}</>;
  if (job.kind === "reconcile") return <>Files #{job.ledger_file_id} · #{job.settlement_file_id} · #{job.bank_file_id}</>;
  return <Link href={`/runs/${job.replay_of_run_id}`}>Run #{job.replay_of_run_id}</Link>;
}

function Result({ job }: { job: JobListRow }) {
  if (job.status !== "done") return job.error ? <span className="small bad-text">{job.error}</span> : <span className="dim">—</span>;
  if (job.kind === "parse_file") {
    return job.file_parse_status === "rejected"
      ? <span className="bad-text">rejected</span>
      : <>{grouped(job.file_rows)} {ROW_NOUN[job.file_kind ?? "ledger"]}</>;
  }
  if (!job.run_id) return <span className="dim">—</span>;
  if (job.kind === "reconcile") {
    return job.run_status === "finished"
      ? <><Link href={`/runs/${job.run_id}`}>Run #{job.run_id}</Link> · {job.run_matches} matches, {job.run_exceptions} exceptions</>
      : <><Link href={`/runs/${job.run_id}`}>Run #{job.run_id}</Link> · <span className="bad-text">{job.run_status}</span></>;
  }
  return (
    <><Link href={`/runs/${job.run_id}`}>Run #{job.run_id}</Link> ·{" "}
      <strong className={job.run_replay_outcome === "identical" ? "ok-text" : "bad-text"}>{job.run_replay_outcome}</strong></>
  );
}

/**
 * Jobs, their attempts and errors. The requeue button is shown to controllers only, but that is
 * presentation: the POST route and the database function both refuse anyone else (D-074, D-077).
 */
export default async function JobsPage({ searchParams }: { searchParams: SearchParams }) {
  const staff = await requirePageStaff();
  const [jobs, counts, requeues, flash] = await Promise.all([
    listJobsWithResults(), jobCounts(), listRequeues(), flashFrom(searchParams)]);
  const canRequeue = staff.role === "controller";

  return (
    <Shell staff={staff} active="jobs">
      <main className="page">
        <div className="page-head">
          <div>
            <h1>Jobs</h1>
            <p className="lead">Parsing, reconciliation and replay all run as jobs on the worker. Every attempt and error is kept.</p>
          </div>
        </div>
        <Flash {...flash} />

        <section className="card counts">
          {([["Total", counts.total], ["Queued", counts.queued], ["Running", counts.running], ["Failed", counts.failed], ["Done", counts.done]] as const).map(([label, n]) => (
            <div key={label}><span className="eyebrow">{label}</span><span className="figure">{n}</span></div>
          ))}
        </section>

        <section className="card">
          <div className="card-head"><h2>All jobs</h2><span className="hint">Newest first</span></div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Job</th><th>Kind</th><th>Target</th><th>Result</th><th>Status</th><th className="num">Attempts</th>
                  <th>Requested by</th><th>Queued (UTC)</th><th>Action</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id}>
                    <td className="id strong" style={{ color: "var(--ink)" }}>#{job.id}</td>
                    <td>{KIND[job.kind] ?? job.kind}</td>
                    <td><Target job={job} /></td>
                    <td><Result job={job} /></td>
                    <td>
                      <StatusChip status={job.status} />
                      <div className="small"><QueueStatus status={job.status} overdue={job.overdue} /></div>
                    </td>
                    <td className="num" title={`budget ${job.max_attempts}`}>{job.attempts} / {job.max_attempts}</td>
                    <td><Actor name={job.requested_by_name} role={job.requested_by_role} /></td>
                    <td className="mono small">{formatTimestamp(job.queued_at).replace(" UTC", "").slice(0, 16)}</td>
                    <td>
                      {job.status === "failed" && canRequeue ? (
                        <form action={`/api/jobs/${job.id}/requeue`} method="post">
                          <button type="submit" className="button small">Requeue</button>
                        </form>
                      ) : <span className="dim">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <p className="info" style={{ marginTop: "var(--space-4)", maxWidth: "48rem" }}>
          <InfoIcon /> Failed jobs can be requeued by a controller. Requeueing is idempotent and never resets
          a job&apos;s attempt count; a job that has used its whole budget cannot be requeued.
        </p>

        <section className="card" style={{ marginTop: "var(--space-4)" }}>
          <div className="card-head"><h2>Requeue log</h2><span className="hint">Append-only: who requeued what, and the error it replaced</span></div>
          {requeues.length === 0 ? <p className="empty-state">No requeues.</p> : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Job</th><th>By</th><th>When</th><th className="num">Attempts then</th><th>Error it replaced</th></tr></thead>
                <tbody>
                  {requeues.map((r, i) => (
                    <tr key={i}>
                      <td className="id">#{r.job_id}</td>
                      <td>{r.requeued_by}</td>
                      <td className="mono small">{formatTimestamp(r.requeued_at)}</td>
                      <td className="num">{r.attempts_at_requeue}</td>
                      <td className="small">{r.previous_error}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>
    </Shell>
  );
}
