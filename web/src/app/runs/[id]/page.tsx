import Link from "next/link";
import { notFound } from "next/navigation";
import { Flash, flashFrom, Shell, StatusChip, type SearchParams } from "@/components/shell";
import { parseId, requirePageStaff } from "@/lib/auth";
import { formatTimestamp } from "@/lib/format";
import { failedReplayJobs, getRun, pendingReplays, replaysOf, type RunOverview } from "@/lib/queries";

export const dynamic = "force-dynamic";

function ReplayLine({ replay }: { replay: RunOverview }) {
  // Four distinct events, never flattened (D-078):
  //   identical  the replay ran and reproduced the original byte for byte
  //   different  the replay RAN and produced a different result (drift): both hashes shown
  //   failed     the replay run was recorded but FAILED TO RUN: its error shown
  //   running    still computing
  return (
    <tr>
      <td className="mono"><Link href={`/runs/${replay.run_id}`}>#{replay.run_id}</Link></td>
      <td><StatusChip status={replay.replay_outcome} /></td>
      <td className="mono">{formatTimestamp(replay.started_at)}</td>
      <td>
        {replay.replay_outcome === "identical" ? (
          <span className="mono small">{replay.result_sha256}</span>
        ) : replay.replay_outcome === "different" ? (
          <div className="mono small">
            <div>original <span className="bad-text">{replay.original_result_sha256}</span></div>
            <div>replay&nbsp;&nbsp; <span className="bad-text">{replay.result_sha256}</span></div>
            <div className="dim">Drift: the same inputs and engine version produced a different result.</div>
          </div>
        ) : replay.replay_outcome === "failed" ? (
          <div className="small"><span className="dim">Replay failed to run:</span> {replay.error}</div>
        ) : <span className="dim">computing…</span>}
      </td>
    </tr>
  );
}

export default async function RunPage({ params, searchParams }: {
  params: Promise<{ id: string }>; searchParams: SearchParams;
}) {
  const staff = await requirePageStaff();
  const runId = parseId((await params).id);
  if (!runId) notFound();
  const run = await getRun(runId);
  if (!run) notFound();
  const [replays, pending, refused, flash] = await Promise.all([
    replaysOf(runId), pendingReplays(runId), failedReplayJobs(runId), flashFrom(searchParams)]);

  return (
    <Shell staff={staff}>
      <main className="page">
        <h1>Run #{run.run_id} <StatusChip status={run.status} /></h1>
        <Flash {...flash} />
        {run.status === "failed" ? (
          <p className="flash bad">This run failed: {run.error}</p>
        ) : null}
        {run.replay_of_run_id ? (
          <p className="sub">
            Replay of <Link href={`/runs/${run.replay_of_run_id}`}>run #{run.replay_of_run_id}</Link>:{" "}
            <StatusChip status={run.replay_outcome} />
          </p>
        ) : null}

        <h2>Record</h2>
        <dl className="facts">
          <dt>Engine version</dt><dd className="mono">{run.engine_version}</dd>
          <dt>Engine commit</dt><dd className="mono">{run.engine_git_sha ?? "not recorded"}</dd>
          <dt>Started</dt><dd className="mono">{formatTimestamp(run.started_at)}</dd>
          <dt>Finished</dt><dd className="mono">{formatTimestamp(run.finished_at)}</dd>
          <dt>Result SHA-256</dt><dd className="mono">{run.result_sha256 ?? "none"}</dd>
          <dt>Ledger file</dt><dd className="mono">#{run.ledger_file_id} {run.ledger_sha256}</dd>
          <dt>Settlement file</dt><dd className="mono">#{run.settlement_file_id} {run.settlement_sha256}</dd>
          <dt>Bank file</dt><dd className="mono">#{run.bank_file_id} {run.bank_sha256}</dd>
        </dl>

        {run.status === "finished" ? (
          <>
            <h2>Result</h2>
            <dl className="facts">
              <dt>Matches</dt><dd className="num">{run.matches}</dd>
              <dt>exact</dt><dd className="num">{run.matches_exact}</dd>
              <dt>gross_net</dt><dd className="num">{run.matches_gross_net}</dd>
              <dt>many_to_one</dt><dd className="num">{run.matches_many_to_one}</dd>
              <dt>Exceptions</dt><dd className="num">{run.exceptions}</dd>
              <dt>Open</dt><dd className="num">{run.exceptions_open}</dd>
            </dl>
            <p><Link href={`/runs/${run.run_id}/queue`}>Open the exceptions queue →</Link></p>
          </>
        ) : null}

        <h2>Replay</h2>
        <p className="sub">
          A replay recomputes this run from its stored files with the same engine version and
          records the result as a new run. It is queued for the worker; this page shows it when it lands.
        </p>
        {run.status === "finished" ? (
          <form action={`/api/runs/${run.run_id}/replay`} method="post">
            <button type="submit" className="button">Replay this run</button>
          </form>
        ) : null}
        {pending.length > 0 ? (
          <p className="flash neutral">
            Replay pending: job {pending.map((j) => `#${j.id} (${j.status})`).join(", ")}.
          </p>
        ) : null}
        {refused.map((job) => (
          <p key={job.id} className="flash bad">
            Replay job #{job.id} was refused and produced no run: {job.error}
          </p>
        ))}
        {replays.length > 0 ? (
          <table className="table">
            <thead><tr><th>Replay run</th><th>Outcome</th><th>Started</th><th>Detail</th></tr></thead>
            <tbody>{replays.map((r) => <ReplayLine key={r.run_id} replay={r} />)}</tbody>
          </table>
        ) : <p className="dim">No replays yet.</p>}
      </main>
    </Shell>
  );
}
