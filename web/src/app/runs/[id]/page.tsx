import Link from "next/link";
import { notFound } from "next/navigation";
import {
  Actor, Flash, flashFrom, QueueStatus, ReasonChip, ReplayIcon, Shell, ShieldIcon, StatusChip, type SearchParams,
} from "@/components/shell";
import { parseId, requirePageStaff } from "@/lib/auth";
import { formatTimestamp } from "@/lib/format";
import { grouped, PASS_SUMMARY, REASON_SUMMARY } from "@/lib/present";
import {
  exceptionReasons, failedReplayJobs, getRun, pendingReplays, replaysOf, runFigures, type RunOverview,
} from "@/lib/queries";

export const dynamic = "force-dynamic";

/** One replay of this run. Four distinct events, never flattened (D-078):
 *   identical  the replay ran and reproduced the original byte for byte
 *   different  the replay RAN and produced a different result (drift): both hashes shown
 *   failed     the replay run was recorded but FAILED TO RUN: its error shown
 *   running    still computing */
function ReplayEvent({ replay }: { replay: RunOverview }) {
  return (
    <li>
      <div className="row-line">
        <span className="grow">
          <Link className="mono" href={`/runs/${replay.run_id}`}>Run #{replay.run_id}</Link>{" "}
          <span className="muted small">{formatTimestamp(replay.started_at)}</span>
        </span>
        <StatusChip status={replay.replay_outcome} />
      </div>
      {replay.replay_outcome === "different" ? (
        <>
          <p className="small bad-text" style={{ marginTop: "var(--space-2)" }}>
            Drift: the same inputs and engine version produced a different result.
          </p>
          <div className="eyebrow" style={{ marginTop: "var(--space-3)" }}>Original</div>
          <div className="hash-box">{replay.original_result_sha256}</div>
          <div className="eyebrow" style={{ marginTop: "var(--space-3)" }}>Replay</div>
          <div className="hash-box bad">{replay.result_sha256}</div>
        </>
      ) : replay.replay_outcome === "failed" ? (
        <p className="small" style={{ marginTop: "var(--space-2)" }}><span className="muted">Replay failed to run:</span> {replay.error}</p>
      ) : replay.replay_outcome === "running" ? (
        <p className="small muted" style={{ marginTop: "var(--space-2)" }}>Computing…</p>
      ) : null}
    </li>
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
  const [replays, pending, refused, flash, figures, reasons] = await Promise.all([
    replaysOf(runId), pendingReplays(runId), failedReplayJobs(runId), flashFrom(searchParams),
    runFigures(runId), exceptionReasons(runId)]);
  const original = run.replay_of_run_id ? await getRun(run.replay_of_run_id) : null;
  const shown = replays.find((r) => r.replay_outcome === "identical") ?? replays[0] ?? null;
  const finished = run.status === "finished";

  return (
    <Shell staff={staff} active="runs">
      <main className="page">
        <div className="crumbs"><Link href="/runs">Runs</Link><span className="slash">/</span><span className="here">Run #{run.run_id}</span></div>
        <div className="page-head">
          <div>
            <div className="title-row">
              <h1 style={{ fontSize: "var(--text-xl)" }}>Run #{run.run_id}</h1>
              <StatusChip status={run.status} />
              {run.replay_of_run_id
                ? <span className="chip neutral">Replay of #{run.replay_of_run_id}</span>
                : <span className="chip neutral">Original</span>}
            </div>
            <p className="lead">{figures?.period_label ?? "Statement period unknown"} · reconciled by engine {run.engine_version}</p>
          </div>
          <div className="actions">
            {finished ? <Link className="button" href={`/runs/${run.run_id}/queue`}>View {run.exceptions} exceptions</Link> : null}
            {finished ? (
              <form action={`/api/runs/${run.run_id}/replay`} method="post">
                <button type="submit" className="button primary"><ReplayIcon /> Request replay</button>
              </form>
            ) : null}
          </div>
        </div>

        <Flash {...flash} />
        {run.status === "failed" ? <p className="flash bad">This run failed: {run.error}</p> : null}
        {pending.map((job) => (
          <p key={job.id} className="flash neutral">
            Replay pending (job #{job.id}). <QueueStatus status={job.status} overdue={job.overdue} />
          </p>
        ))}
        {refused.map((job) => (
          <p key={job.id} className="flash bad">Replay job #{job.id} was refused and produced no run: {job.error}</p>
        ))}

        <section className="card" style={{ marginBottom: "var(--space-4)" }}>
          <dl className="facts-strip">
            <div><dt>Engine</dt><dd className="mono">{run.engine_version}</dd></div>
            <div><dt>Requested by</dt><dd><Actor name={run.requested_by_name} role={run.requested_by_role} /></dd></div>
            <div><dt>Started</dt><dd className="mono">{formatTimestamp(run.started_at)}</dd></div>
            <div><dt>Result</dt><dd>{finished ? `${run.matches} matches · ${run.exceptions} exceptions` : run.status}</dd></div>
          </dl>
        </section>

        <div className="grid main-side">
          <div className="col">
            {finished ? (
              <section className="card">
                <div className="card-head">
                  <h2>How the {run.matches} matches were made</h2>
                  <span className="hint">Each pass only sees what earlier passes left</span>
                </div>
                <div style={{ padding: "0 var(--space-5)" }}>
                  <div className="bar lg" aria-hidden="true">
                    <span className="exact" style={{ flexGrow: run.matches_exact }} />
                    <span className="gross_net" style={{ flexGrow: run.matches_gross_net }} />
                    <span className="many_to_one" style={{ flexGrow: run.matches_many_to_one }} />
                  </div>
                </div>
                <ul className="rows">
                  {([["exact", run.matches_exact], ["gross_net", run.matches_gross_net], ["many_to_one", run.matches_many_to_one]] as const).map(([pass, count]) => (
                    <li key={pass} className="row-line">
                      <span className={`key ${pass}`} />
                      <div className="grow">
                        <h3>{PASS_SUMMARY[pass].label}</h3>
                        <p>{PASS_SUMMARY[pass].text}</p>
                      </div>
                      <span className="count">{count}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {finished ? (
              <section className="card">
                <div className="card-head">
                  <h2>Exceptions raised</h2>
                  <Link className="small strong" href={`/runs/${run.run_id}/queue`} style={{ color: "var(--accent)" }}>Open queue →</Link>
                </div>
                {reasons.length === 0 ? <p className="empty-state">No exceptions: every row reconciled.</p> : (
                  <ul className="rows">
                    {reasons.map((r) => (
                      <li key={r.suggested_reason} className="row-line" style={{ alignItems: "center" }}>
                        <ReasonChip reason={r.suggested_reason} />
                        <span className="grow muted small">{REASON_SUMMARY[r.suggested_reason] ?? ""}</span>
                        <span className="count">{r.exceptions}</span>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="small muted" style={{ padding: "0 var(--space-5) var(--space-4)" }}>
                  {run.exceptions_open} of {run.exceptions} open.
                </p>
              </section>
            ) : null}

            <section className="card">
              <details className="record">
                <summary>Full record</summary>
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
              </details>
            </section>
          </div>

          <div className="col">
            <section className="card card-pad">
              <div className="row-line" style={{ alignItems: "center", marginBottom: "var(--space-3)" }}>
                <h2>Replay verification</h2>
                {original ? <StatusChip status={run.replay_outcome} /> : shown ? <StatusChip status={shown.replay_outcome} /> : <span className="chip neutral">Not replayed</span>}
              </div>
              {original ? (
                <>
                  <p className="muted">
                    This run recomputed <Link href={`/runs/${original.run_id}`}>run #{original.run_id}</Link> from the
                    same three stored files.
                  </p>
                  <div className="eyebrow" style={{ marginTop: "var(--space-4)" }}>Original · run #{original.run_id}</div>
                  <div className="hash-box">{run.original_result_sha256}</div>
                  <div className="eyebrow" style={{ marginTop: "var(--space-3)" }}>Replay · run #{run.run_id}</div>
                  <div className={`hash-box${run.replay_outcome === "different" ? " bad" : ""}`}>{run.result_sha256 ?? "no result"}</div>
                  {run.replay_outcome === "different" ? (
                    <p className="small bad-text" style={{ marginTop: "var(--space-3)" }}>Drift: the same inputs and engine version produced a different result.</p>
                  ) : null}
                </>
              ) : shown && shown.replay_outcome === "identical" ? (
                <>
                  <p className="muted">
                    <Link href={`/runs/${shown.run_id}`}>Run #{shown.run_id}</Link> recomputed this run from the same three
                    stored files and produced the same bytes.
                  </p>
                  <div className="eyebrow" style={{ marginTop: "var(--space-4)" }}>Original · run #{run.run_id}</div>
                  <div className="hash-box">{run.result_sha256}</div>
                  <div className="eyebrow" style={{ marginTop: "var(--space-3)" }}>Replay · run #{shown.run_id}</div>
                  <div className="hash-box">{shown.result_sha256}</div>
                </>
              ) : (
                <>
                  <p className="muted">
                    {finished ? "Request a replay to recompute this run from its stored files and compare the result." : "Only a finished run can be replayed."}
                  </p>
                  {run.result_sha256 ? (
                    <>
                      <div className="eyebrow" style={{ marginTop: "var(--space-4)" }}>Result · run #{run.run_id}</div>
                      <div className="hash-box">{run.result_sha256}</div>
                    </>
                  ) : null}
                </>
              )}
              <p className="small muted" style={{ marginTop: "var(--space-4)" }}>
                The engine is a pure function of the stored files and its version: no clock, database,
                randomness or locale.
              </p>
              {replays.length > 0 ? (
                <>
                  <div className="eyebrow" style={{ marginTop: "var(--space-5)" }}>Every replay of this run</div>
                  <ul className="rows" style={{ margin: "0 calc(-1 * var(--space-5))" }}>
                    {replays.map((r) => <ReplayEvent key={r.run_id} replay={r} />)}
                  </ul>
                </>
              ) : null}
            </section>

            {figures ? (
              <section className="card">
                <div className="card-head"><h2>Inputs</h2></div>
                <ul className="rows">
                  {([
                    ["Ledger", run.ledger_file_id, figures.ledger_name, `${grouped(figures.ledger_rows)} rows`],
                    ["Settlement", run.settlement_file_id, figures.settlement_name, `${grouped(figures.settlement_rows)} lines`],
                    ["Bank", run.bank_file_id, figures.bank_name, `${grouped(figures.bank_entries)} entries`],
                  ] as const).map(([label, id, name, rows]) => (
                    <li key={label} className="row-line">
                      <div className="grow">
                        <div className="small strong">{label} · file #{id}</div>
                        <div className="mono small" style={{ overflowWrap: "anywhere" }}>{name}</div>
                      </div>
                      <span className="mono small muted">{rows}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {finished ? (
              <p className="info"><ShieldIcon /> The database re-checked the arithmetic of every match before accepting this run as finished.</p>
            ) : null}
          </div>
        </div>
      </main>
    </Shell>
  );
}
