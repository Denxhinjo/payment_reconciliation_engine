import Link from "next/link";
import { Actor, Flash, flashFrom, Shell, StatusChip, type SearchParams } from "@/components/shell";
import { requirePageStaff } from "@/lib/auth";
import { formatTimestamp, shortHash } from "@/lib/format";
import { listRuns } from "@/lib/queries";

export const dynamic = "force-dynamic";

/** Every run, including failed ones and replays. A failed run is listed as failed with its
 * reason; it never disappears (D-078). All figures come from run_overview. */
export default async function RunsPage({ searchParams }: { searchParams: SearchParams }) {
  const staff = await requirePageStaff();
  const [runs, flash] = await Promise.all([listRuns(), flashFrom(searchParams)]);

  return (
    <Shell staff={staff}>
      <main className="page">
        <h1>Reconciliation runs</h1>
        <Flash {...flash} />
        {runs.length === 0 ? (
          <p className="sub">No runs yet. Upload three files and request a reconciliation.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Run</th><th>Status</th><th>Engine</th><th>Requested by</th><th>Started</th>
                <th className="num">Matches</th><th className="num">Exceptions</th>
                <th className="num">Open</th><th>Result hash</th><th>Replay of</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.run_id}>
                  <td className="mono"><Link href={`/runs/${run.run_id}`}>#{run.run_id}</Link></td>
                  <td>
                    <StatusChip status={run.status} />
                    {run.status === "failed" ? <div className="dim small">{run.error}</div> : null}
                  </td>
                  <td className="mono">{run.engine_version}</td>
                  <td><Actor name={run.requested_by_name} role={run.requested_by_role} /></td>
                  <td className="mono">{formatTimestamp(run.started_at)}</td>
                  <td className="num">{run.matches}</td>
                  <td className="num">{run.exceptions}</td>
                  <td className="num">{run.exceptions_open}</td>
                  <td className="mono">{shortHash(run.result_sha256)}</td>
                  <td>
                    {run.replay_of_run_id ? (
                      <>
                        <Link className="mono" href={`/runs/${run.replay_of_run_id}`}>#{run.replay_of_run_id}</Link>{" "}
                        <StatusChip status={run.replay_outcome} />
                      </>
                    ) : <span className="dim">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </main>
    </Shell>
  );
}
