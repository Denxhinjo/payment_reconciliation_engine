import { Flash, flashFrom, Shell, StatusChip, type SearchParams } from "@/components/shell";
import { requirePageStaff } from "@/lib/auth";
import { formatTimestamp } from "@/lib/format";
import { listJobs, listRequeues } from "@/lib/queries";

export const dynamic = "force-dynamic";

/**
 * Jobs, their attempts and errors. The requeue button is shown to controllers only, but that is
 * presentation: the POST route and the database function both refuse anyone else (D-074, D-077).
 */
export default async function JobsPage({ searchParams }: { searchParams: SearchParams }) {
  const staff = await requirePageStaff();
  const [jobs, requeues, flash] = await Promise.all([listJobs(), listRequeues(), flashFrom(searchParams)]);
  const canRequeue = staff.role === "controller";

  return (
    <Shell staff={staff}>
      <main className="page">
        <h1>Jobs</h1>
        <Flash {...flash} />
        <p className="sub">
          Attempts count when a worker claims a job, so a job that crashes its worker still counts.
          Requeue puts a failed job back in the queue without resetting its attempts; a job that has
          used its whole budget cannot be requeued.
        </p>
        <table className="table">
          <thead>
            <tr><th>Job</th><th>Kind</th><th>Status</th><th className="num">Attempts</th><th>Error</th><th>Created</th><th /></tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td className="mono">#{job.id}</td>
                <td className="mono">{job.kind}</td>
                <td><StatusChip status={job.status} /></td>
                <td className="num">{job.attempts} / {job.max_attempts}</td>
                <td className="small">{job.error}</td>
                <td className="mono small">{formatTimestamp(job.created_at)}</td>
                <td>
                  {job.status === "failed" && canRequeue ? (
                    <form action={`/api/jobs/${job.id}/requeue`} method="post">
                      <button type="submit" className="button">Requeue</button>
                    </form>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <h2>Requeue log</h2>
        {requeues.length === 0 ? <p className="dim">No requeues.</p> : (
          <table className="table">
            <thead><tr><th>Job</th><th>By</th><th>When</th><th className="num">Attempts then</th><th>Error it replaced</th></tr></thead>
            <tbody>
              {requeues.map((r, i) => (
                <tr key={i}>
                  <td className="mono">#{r.job_id}</td>
                  <td>{r.requeued_by}</td>
                  <td className="mono small">{formatTimestamp(r.requeued_at)}</td>
                  <td className="num">{r.attempts_at_requeue}</td>
                  <td className="small">{r.previous_error}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </main>
    </Shell>
  );
}
