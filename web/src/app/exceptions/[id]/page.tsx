import Link from "next/link";
import { notFound } from "next/navigation";
import { NoteField } from "@/components/client";
import { Actor, Flash, flashFrom, InfoIcon, ReasonChip, Shell, StatusChip, type SearchParams } from "@/components/shell";
import { parseId, requirePageStaff } from "@/lib/auth";
import { formatMinor, formatTimestamp } from "@/lib/format";
import { exceptionHeadline } from "@/lib/present";
import {
  evidence, exceptionFigures, getException, getRun, reasonCodes, resolutionHistory, runFigures, type EvidenceRow,
} from "@/lib/queries";

export const dynamic = "force-dynamic";

/** Which source the break is about, for emphasis only. */
const ATTENTION: Record<string, "ledger" | "settlement" | "bank"> = {
  missing_from_bank: "bank", amount_mismatch: "bank", unknown_deposit: "bank", unexplained_debit: "bank",
  possible_duplicate: "ledger", missing_from_ledger: "ledger",
};

function RowList({ rows }: { rows: EvidenceRow[] }) {
  return (
    <div className="rowlist">
      {rows.map((r) => (
        <div key={`${r.source}-${r.identifier}`} className="line">
          <span className="id-block"><span className="mono small">{r.identifier}</span><span className="small muted">{r.on_date}{r.source === "settlement" ? "" : ` · ${r.detail}`}</span></span>
          <span className="mono">{formatMinor(r.amount_minor)}</span>
        </div>
      ))}
    </div>
  );
}

function ResolutionForm({ action, supersedes, reasons, title }: {
  action: string; supersedes?: string; reasons: { code: string; label: string }[]; title: string;
}) {
  return (
    <section className="card card-pad">
      <h2>{title}</h2>
      <p className="small muted" style={{ margin: "var(--space-1) 0 var(--space-4)" }}>
        {supersedes
          ? "Resolutions are never edited: a correction is a new resolution that supersedes the one in force, and the full history stays visible."
          : "Needs a reason code and a note of at least 10 characters."}
      </p>
      <form action={action} method="post">
        {supersedes ? <input type="hidden" name="supersedes_id" value={supersedes} /> : null}
        <label className="field">
          <span>Reason code</span>
          <select name="reason_code" required defaultValue="">
            <option value="" disabled>Choose a reason…</option>
            {reasons.map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}
          </select>
        </label>
        <NoteField submitLabel={supersedes ? `Correct resolution #${supersedes}` : "Resolve exception"} />
      </form>
    </section>
  );
}

export default async function ExceptionPage({ params, searchParams }: {
  params: Promise<{ id: string }>; searchParams: SearchParams;
}) {
  const staff = await requirePageStaff();
  const exceptionId = parseId((await params).id);
  if (!exceptionId) notFound();
  const [item, figures] = await Promise.all([getException(exceptionId), exceptionFigures(exceptionId)]);
  if (!item || !figures) notFound();
  const [rows, history, reasons, flash, run, period] = await Promise.all([
    evidence(exceptionId), resolutionHistory(exceptionId), reasonCodes(), flashFrom(searchParams),
    getRun(item.run_id), runFigures(item.run_id)]);
  const bySource = (source: EvidenceRow["source"]) => rows.filter((r) => r.source === source);
  const attention = ATTENTION[item.suggested_reason];
  const reversed = figures.shortfall_minor?.startsWith("-");

  return (
    <Shell staff={staff} active="exceptions">
      <main className="page">
        <div className="crumbs">
          <Link href={`/runs/${item.run_id}/queue`}>Exceptions</Link><span className="slash">/</span>
          <span className="here">#{item.ordinal}</span>
        </div>
        <div className="title-row" style={{ marginBottom: "var(--space-2)" }}>
          <ReasonChip reason={item.suggested_reason} /> <StatusChip status={item.status} />
        </div>
        <h1>{exceptionHeadline(figures)}</h1>
        <p className="lead muted" style={{ margin: "var(--space-2) 0 var(--space-5)" }}>
          <span className="mono">{figures.reference}</span> · {figures.ledger_rows} ledger · {figures.settlement_rows} settlement
          · {figures.bank_rows} bank rows · raised by <Link href={`/runs/${item.run_id}`}>run #{item.run_id}</Link>
          {run ? <> on <span className="mono">{formatTimestamp(run.finished_at ?? run.started_at)}</span></> : null}
        </p>
        <Flash {...flash} />

        <div className="grid main-side">
          <div className="col">
            <section className="card">
              <div className="card-head">
                <h2>Evidence</h2>
                <span className="hint">The rows this exception allocates, from each source</span>
              </div>
              <div className="evidence">
                <section className={attention === "ledger" ? "attention" : undefined}>
                  <div className="eyebrow">Internal ledger</div>
                  {bySource("ledger").length === 0 ? <p className="empty">No ledger row</p> : (
                    <>
                      <RowList rows={bySource("ledger")} />
                      <div className="total">{formatMinor(figures.ledger_minor)}</div>
                      <div className="small muted">ledger, EUR</div>
                    </>
                  )}
                </section>
                <section className={attention === "settlement" ? "attention" : undefined}>
                  <div className="eyebrow">Orrery settlement</div>
                  {bySource("settlement").length === 0 ? <p className="empty">No settlement line</p> : (
                    <>
                      <RowList rows={bySource("settlement")} />
                      <div className="line" style={{ marginTop: "var(--space-3)" }}><span>Gross</span><span className="mono">{formatMinor(figures.settlement_gross_minor)}</span></div>
                      <div className="line"><span>Stated fee</span><span className="mono">−{formatMinor(figures.settlement_fee_minor)}</span></div>
                      <div className="total">{formatMinor(figures.settlement_net_minor)}</div>
                      <div className="small muted">net payout, EUR</div>
                    </>
                  )}
                </section>
                <section className={attention === "bank" ? "attention" : undefined}>
                  <div className="eyebrow">Demo Bank statement</div>
                  {bySource("bank").length === 0 ? <p className="empty">No bank entry</p> : (
                    <>
                      <RowList rows={bySource("bank")} />
                      <div className="total">{formatMinor(figures.bank_minor)}</div>
                      <div className="small muted">received, EUR</div>
                    </>
                  )}
                </section>
              </div>
            </section>

            <section className="card card-pad">
              <h2 style={{ marginBottom: "var(--space-4)" }}>
                {item.suggested_reason === "amount_mismatch" ? "Why it doesn't reconcile" : "What the engine found"}
              </h2>
              {item.suggested_reason === "amount_mismatch" && figures.expected_minor && figures.received_minor ? (
                <div className="equation">
                  <span className="term"><small>Expected at bank</small><span className="mono">{formatMinor(figures.expected_minor)}</span></span>
                  <span className="op">−</span>
                  <span className="term"><small>Received</small><span className="mono">{formatMinor(figures.received_minor)}</span></span>
                  <span className="op">=</span>
                  <span className="term result">
                    <small>{reversed ? "Received too much" : "Shortfall"}</small>
                    <span className="mono">EUR {formatMinor(figures.amount_at_issue_minor)}</span>
                  </span>
                </div>
              ) : null}
              <p className={item.suggested_reason === "amount_mismatch" ? "explain" : undefined}>{item.explanation}</p>
              {item.suggested_reason === "amount_mismatch" && figures.settlement_rows !== "0" ? (
                <p className="small muted" style={{ marginTop: "var(--space-3)" }}>
                  The fee is taken as Orrery states it; this demo does not check it against a fee schedule
                  (known weakness F8).
                </p>
              ) : null}
            </section>

            <section className="card card-pad">
              <h2 style={{ marginBottom: "var(--space-3)" }}>History</h2>
              <ol className="timeline">
                <li>
                  <span className="dot raised" />
                  <div>
                    <div className="strong">Raised as {item.suggested_reason}</div>
                    <div className="meta">
                      Run #{item.run_id}{run ? <> · <Actor name={run.requested_by_name} role={run.requested_by_role} /> · {formatTimestamp(run.finished_at ?? run.started_at)}</> : null}
                    </div>
                  </div>
                </li>
                {history.map((h) => (
                  <li key={h.resolution_id}>
                    <span className={`dot ${h.in_force ? "in-force" : "superseded"}`} />
                    <div>
                      <div>
                        <span className="strong">{h.reason_label}</span>{" "}
                        <span className={`chip ${h.in_force ? "ok" : "neutral"}`}>{h.in_force ? "in force" : "superseded"}</span>
                      </div>
                      <div className="meta">#{h.resolution_id} · {h.resolved_by} · {formatTimestamp(h.created_at)}</div>
                      <p className="note">{h.note}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </section>
          </div>

          <div className="col">
            {item.status === "open" ? (
              <ResolutionForm title="Resolve this exception" action={`/api/exceptions/${exceptionId}/resolve`} reasons={reasons} />
            ) : (
              <ResolutionForm title="Correct the resolution in force" action={`/api/exceptions/${exceptionId}/correct`}
                supersedes={item.current_resolution_id ?? undefined} reasons={reasons} />
            )}
            <section className="card card-pad">
              <div className="eyebrow" style={{ marginBottom: "var(--space-3)" }}>Related</div>
              <p><Link className="strong" style={{ color: "var(--accent)" }} href={`/runs/${item.run_id}`}>Run #{item.run_id}{period?.period_label ? ` · ${period.period_label}` : ""}</Link></p>
              <p style={{ marginTop: "var(--space-2)" }}>
                <Link className="strong" style={{ color: "var(--accent)" }} href={`/runs/${item.run_id}/queue`}>
                  Back to queue ({figures.other_open_in_run} more open)
                </Link>
              </p>
            </section>
            <p className="info"><InfoIcon /> Resolutions are never edited or deleted. If someone else resolves this first, you&apos;ll be told who got there.</p>
          </div>
        </div>
      </main>
    </Shell>
  );
}
