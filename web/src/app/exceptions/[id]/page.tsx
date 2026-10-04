import Link from "next/link";
import { notFound } from "next/navigation";
import { Flash, flashFrom, Shell, StatusChip, type SearchParams } from "@/components/shell";
import { parseId, requirePageStaff } from "@/lib/auth";
import { formatMinor, formatTimestamp } from "@/lib/format";
import { evidence, getException, reasonCodes, resolutionHistory } from "@/lib/queries";

export const dynamic = "force-dynamic";

function ResolutionForm({ action, supersedes, reasons }: {
  action: string; supersedes?: string; reasons: { code: string; label: string }[];
}) {
  return (
    <form action={action} method="post" className="panel stack">
      {supersedes ? <input type="hidden" name="supersedes_id" value={supersedes} /> : null}
      <label>
        Reason
        <select name="reason_code" required defaultValue="">
          <option value="" disabled>Choose a reason…</option>
          {reasons.map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}
        </select>
      </label>
      <label>
        Note (required, at least 10 characters)
        <textarea name="note" required minLength={10} rows={3} />
      </label>
      <button type="submit" className="button">{supersedes ? `Correct resolution #${supersedes}` : "Resolve"}</button>
    </form>
  );
}

export default async function ExceptionPage({ params, searchParams }: {
  params: Promise<{ id: string }>; searchParams: SearchParams;
}) {
  const staff = await requirePageStaff();
  const exceptionId = parseId((await params).id);
  if (!exceptionId) notFound();
  const item = await getException(exceptionId);
  if (!item) notFound();
  const [rows, history, reasons, flash] = await Promise.all([
    evidence(exceptionId), resolutionHistory(exceptionId), reasonCodes(), flashFrom(searchParams)]);

  return (
    <Shell staff={staff}>
      <main className="page">
        <h1>
          Exception {item.ordinal} of <Link href={`/runs/${item.run_id}`}>run #{item.run_id}</Link>{" "}
          <StatusChip status={item.status} />
        </h1>
        <Flash {...flash} />
        <p><span className="mono">{item.suggested_reason}</span>: {item.explanation}</p>

        <h2>Evidence</h2>
        <table className="table">
          <thead>
            <tr><th>Source</th><th>Identifier</th><th>Date</th><th>Reference</th><th className="num">EUR</th><th>Detail</th></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.source}-${row.identifier}`}>
                <td>{row.source}</td>
                <td className="mono">{row.identifier}</td>
                <td className="mono">{row.on_date}</td>
                <td className="mono">{row.reference}</td>
                <td className="num">{formatMinor(row.amount_minor)}</td>
                <td className="dim">{row.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <h2>Resolution history (oldest first)</h2>
        {history.length === 0 ? <p className="dim">Not resolved yet.</p> : (
          <ol className="history">
            {history.map((h) => (
              <li key={h.resolution_id}>
                <span className="mono">#{h.resolution_id}</span>{" "}
                <span className={`chip ${h.in_force ? "ok" : "neutral"}`}>{h.in_force ? "in force" : "superseded"}</span>{" "}
                <strong>{h.reason_label}</strong> by {h.resolved_by},{" "}
                <span className="mono">{formatTimestamp(h.created_at)}</span>
                <p className="note">{h.note}</p>
              </li>
            ))}
          </ol>
        )}

        {item.status === "open" ? (
          <>
            <h2>Resolve</h2>
            <ResolutionForm action={`/api/exceptions/${exceptionId}/resolve`} reasons={reasons} />
          </>
        ) : (
          <>
            <h2>Correct the resolution in force</h2>
            <p className="sub">
              Resolutions are never edited. A correction is a new resolution that supersedes the one
              in force; the full history stays visible.
            </p>
            <ResolutionForm action={`/api/exceptions/${exceptionId}/correct`}
              supersedes={item.current_resolution_id ?? undefined} reasons={reasons} />
          </>
        )}
      </main>
    </Shell>
  );
}
