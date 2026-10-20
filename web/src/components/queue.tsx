import Link from "next/link";
import { Flash, ReasonChip } from "@/components/shell";
import { formatMinor } from "@/lib/format";
import { exceptionTitle } from "@/lib/present";
import { queueWithFigures, type QueueFilter, type RunFigures, type RunOverview } from "@/lib/queries";

const TABS: { key: QueueFilter; label: string }[] = [
  { key: "open", label: "All open" },
  { key: "review", label: "Needs review" },
  { key: "timing", label: "Timing" },
  { key: "resolved", label: "Resolved" },
];

export function parseFilter(value: unknown): QueueFilter {
  return value === "review" || value === "timing" || value === "resolved" || value === "all" ? value : "open";
}

/** One run's exceptions queue. Counts and amounts come from run_figures and exception_figures. */
export async function QueueView({ run, figures, filter, basePath, flash }: {
  run: RunOverview; figures: RunFigures; filter: QueueFilter; basePath: string;
  flash: { notice?: string | null; error?: string | null };
}) {
  const items = await queueWithFigures(run.run_id, filter);
  const counts: Record<QueueFilter, string> = {
    open: run.exceptions_open, review: figures.open_needing_review, timing: figures.open_timing,
    resolved: figures.resolved, all: run.exceptions,
  };

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <div className="eyebrow">
            <Link href={`/runs/${run.run_id}`} style={{ color: "inherit" }}>Run #{run.run_id}</Link> · {figures.period_label}
          </div>
          <h1>Exceptions queue</h1>
          <p className="lead" style={{ maxWidth: "36rem" }}>
            Only the genuine differences between ledger, settlement and bank. Each one needs a reason
            code and a written note to close.
          </p>
        </div>
        <div className="value-under-review">
          <div className="figure">EUR {formatMinor(figures.open_value_under_review_minor)}</div>
          <div className="small muted">value under review ({figures.open_needing_review} open exceptions needing review)</div>
        </div>
      </div>
      <Flash {...flash} />

      <nav className="tabs" aria-label="Filter">
        {TABS.map((t) => (
          <Link key={t.key} href={`${basePath}?status=${t.key}`} aria-current={filter === t.key ? "page" : undefined}>
            {t.label} <span className="n">{counts[t.key]}</span>
          </Link>
        ))}
      </nav>

      <section className="card">
        {items.length === 0 ? <p className="empty-state">Nothing here.</p> : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>ID</th><th>Reason</th><th>What happened</th><th>Reference</th><th className="num">Amount (EUR)</th><th>Status</th></tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.exception_id}>
                    <td className="id"><Link href={`/exceptions/${item.exception_id}`}>#{item.ordinal}</Link></td>
                    <td><ReasonChip reason={item.suggested_reason} /></td>
                    <td>
                      <Link href={`/exceptions/${item.exception_id}`} className="title" style={{ color: "var(--ink)" }}>{exceptionTitle(item)}</Link>
                      <div className="detail">{item.explanation}</div>
                    </td>
                    <td className="mono small ref">{item.reference}</td>
                    <td className="num amount">{item.amount_at_issue_minor ? formatMinor(item.amount_at_issue_minor) : <span className="dim">—</span>}</td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <span className={`dot ${item.status}`} />
                      <span style={{ textTransform: "capitalize" }}>{item.status}</span>
                      {item.resolved_by ? <div className="small muted">{item.resolved_by} · {item.current_reason_code}</div> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <p className="footnote">
        Resolutions are append-only: a correction supersedes the resolution in force and the full
        history stays visible. If two people act on the same exception at once, exactly one gets
        through and the other is told who got there first. Amounts are the money each break puts in
        question; timing items have none, because the money is expected to arrive (D-097).
      </p>
    </main>
  );
}
