import Link from "next/link";
import { Actor, CheckCircle, Flash, flashFrom, PlusIcon, ReasonChip, Shell, StatusChip, type SearchParams } from "@/components/shell";
import { requirePageStaff } from "@/lib/auth";
import { formatTimestamp } from "@/lib/format";
import { grouped, hashGlance } from "@/lib/present";
import { getRun, latestRunId, listRuns, plantedCaught, plantedItems, runFigures } from "@/lib/queries";

export const dynamic = "force-dynamic";

/** The dashboard: the latest finished original run at a glance, then every run, including failed
 * ones and replays. A failed run is listed as failed with its reason; it never disappears
 * (D-078). Every figure comes from run_overview and run_figures (D-076, D-096). */
export default async function RunsPage({ searchParams }: { searchParams: SearchParams }) {
  const staff = await requirePageStaff();
  const [runs, flash, latestId] = await Promise.all([listRuns(), flashFrom(searchParams), latestRunId()]);
  const [latest, figures, planted, caught] = latestId
    ? await Promise.all([getRun(latestId), runFigures(latestId), plantedItems(latestId), plantedCaught(latestId)])
    : [null, null, [], null];
  const replay = figures?.latest_replay_run_id ? await getRun(figures.latest_replay_run_id) : null;
  const problems = planted.filter((p) => p.category === "planted");
  const timing = planted.filter((p) => p.category === "timing");

  return (
    <Shell staff={staff} active="runs">
      <main className="page">
        <Flash {...flash} />
        {latest && figures ? (
          <>
            <div className="page-head">
              <div>
                <div className="eyebrow">Statement period</div>
                <h1>{figures.period_label}</h1>
                <p className="lead">Internal ledger · Orrery Payments settlement · Demo Bank camt.053 · {figures.currency}</p>
              </div>
              <div className="actions">
                <Link className="button" href={`/runs/${latest.run_id}`}>Latest run</Link>
                <Link className="button primary" href="/upload"><PlusIcon /> New reconciliation</Link>
              </div>
            </div>

            <div className="grid kpis">
              <section className="card kpi">
                <div className="eyebrow">Matches</div>
                <div className="value">{latest.matches}</div>
                <div className="bar" aria-hidden="true">
                  <span className="exact" style={{ flexGrow: latest.matches_exact }} />
                  <span className="gross_net" style={{ flexGrow: latest.matches_gross_net }} />
                  <span className="many_to_one" style={{ flexGrow: latest.matches_many_to_one }} />
                </div>
                <div className="note">{latest.matches_exact} exact · {latest.matches_gross_net} gross/net · {latest.matches_many_to_one} many-to-one</div>
              </section>
              <section className="card kpi">
                <div className="eyebrow">Open exceptions</div>
                <div className="value">{latest.exceptions_open}<span className="of">of {latest.exceptions} raised</span></div>
                <div className="chips">
                  <span className="chip warn">{figures.open_needing_review} need review</span>
                  <span className="chip slate">{figures.open_timing} timing</span>
                </div>
                <Link className="small strong" href={`/runs/${latest.run_id}/queue`} style={{ color: "var(--accent)" }}>Open the queue →</Link>
              </section>
              <section className="card kpi">
                <div className="eyebrow">Replay check</div>
                {replay ? (
                  <>
                    <div className="value word">
                      {replay.replay_outcome === "identical" ? <CheckCircle /> : null}
                      <span style={{ textTransform: "capitalize" }}>{replay.replay_outcome}</span>
                    </div>
                    <div className="note">
                      Run #{replay.run_id}{" "}
                      {replay.replay_outcome === "identical" ? `reproduced run #${latest.run_id} byte for byte`
                        : replay.replay_outcome === "different" ? `did not reproduce run #${latest.run_id}`
                        : replay.replay_outcome === "failed" ? "failed to run" : "is computing"}
                    </div>
                    <div className="note mono" style={{ marginTop: "var(--space-2)" }}>sha256 {hashGlance(replay.result_sha256)}</div>
                  </>
                ) : (
                  <>
                    <div className="value word">Not replayed</div>
                    <div className="note">Request a replay from the run page to prove it reproduces.</div>
                  </>
                )}
              </section>
              <section className="card kpi">
                <div className="eyebrow">Rows compared</div>
                <div className="value">{grouped(figures.rows_compared)}</div>
                <div className="note">{grouped(figures.ledger_rows)} ledger · {grouped(figures.settlement_rows)} settlement · {grouped(figures.bank_entries)} bank</div>
              </section>
            </div>
          </>
        ) : (
          <div className="page-head">
            <div>
              <h1>Reconciliation runs</h1>
              <p className="lead">No finished run yet. Upload the three files and request a reconciliation.</p>
            </div>
            <div className="actions"><Link className="button primary" href="/upload"><PlusIcon /> New reconciliation</Link></div>
          </div>
        )}

        <section className="card">
          <div className="card-head">
            <h2>Reconciliation runs</h2>
            <span className="hint">Every run is kept, including replays and failures</span>
          </div>
          {runs.length === 0 ? <p className="empty-state">No runs yet.</p> : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Run</th><th>Status</th><th>Engine</th><th>Requested by</th><th>Started (UTC)</th>
                    <th className="num">Matches</th><th className="num">Exceptions</th><th className="num">Open</th>
                    <th>Result hash</th><th>Replay</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => (
                    <tr key={run.run_id}>
                      <td className="id"><Link href={`/runs/${run.run_id}`}>#{run.run_id}</Link></td>
                      <td>
                        <StatusChip status={run.status} />
                        {run.status === "failed" ? <div className="dim small">{run.error}</div> : null}
                      </td>
                      <td className="mono">{run.engine_version}</td>
                      <td><Actor name={run.requested_by_name} role={run.requested_by_role} /></td>
                      <td className="mono">{formatTimestamp(run.started_at).replace(" UTC", "")}</td>
                      <td className="num">{run.matches}</td>
                      <td className="num">{run.exceptions}</td>
                      <td className="num">{run.exceptions_open}</td>
                      <td className="mono dim">{hashGlance(run.result_sha256)}</td>
                      <td>
                        {run.replay_of_run_id ? (
                          <>
                            of <Link className="mono" href={`/runs/${run.replay_of_run_id}`}>#{run.replay_of_run_id}</Link>{" "}
                            <StatusChip status={run.replay_outcome} />
                          </>
                        ) : <span className="muted">Original</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {problems.length > 0 ? (
          <div className="grid wide-narrow" style={{ marginTop: "var(--space-4)" }}>
            <section className="card">
              <div className="card-head">
                <div>
                  <h2>Planted problems</h2>
                  <div className="sub">Errors deliberately seeded into the demo month (demo-data/2026-09/planted.json)</div>
                </div>
                {caught ? (
                  <span className={`chip big ${caught.caught === caught.total ? "ok" : "bad"}`}>
                    {caught.caught} of {caught.total} caught
                  </span>
                ) : null}
              </div>
              <ul className="rows">
                {problems.map((p) => (
                  <li key={p.item_id} className="row-line">
                    <span className="label">{p.item_id}</span>
                    <div className="grow">
                      <h3>{p.title}</h3>
                      <p>{p.description}</p>
                    </div>
                    {p.caught ? (
                      p.exception_id
                        ? <Link href={`/exceptions/${p.exception_id}`}><ReasonChip reason={p.expected_outcome} /></Link>
                        : <ReasonChip reason={p.expected_outcome} label={`${p.expected_outcome} match`} />
                    ) : <span className="chip bad">not caught</span>}
                  </li>
                ))}
              </ul>
            </section>
            <section className="card">
              <div className="card-head">
                <div>
                  <h2>Expected timing differences</h2>
                  <div className="sub">Not errors — money that lands after the period closes</div>
                </div>
              </div>
              <ul className="rows">
                {timing.map((p) => (
                  <li key={p.item_id} className="row-line">
                    <span className="label">{p.item_id}</span>
                    <div className="grow"><p style={{ color: "var(--text)", fontSize: "var(--text-sm)" }}>{p.description}</p></div>
                    {p.caught && p.exception_id
                      ? <Link href={`/exceptions/${p.exception_id}`}><ReasonChip reason="timing" /></Link>
                      : <span className="chip bad">not raised</span>}
                  </li>
                ))}
              </ul>
            </section>
          </div>
        ) : null}
      </main>
    </Shell>
  );
}
