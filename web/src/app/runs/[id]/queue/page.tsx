import Link from "next/link";
import { notFound } from "next/navigation";
import { Flash, flashFrom, Shell, StatusChip, type SearchParams } from "@/components/shell";
import { parseId, requirePageStaff } from "@/lib/auth";
import { exceptionQueue, getRun } from "@/lib/queries";

export const dynamic = "force-dynamic";

const FILTERS = ["open", "resolved", "all"] as const;

export default async function QueuePage({ params, searchParams }: {
  params: Promise<{ id: string }>; searchParams: SearchParams;
}) {
  const staff = await requirePageStaff();
  const runId = parseId((await params).id);
  if (!runId) notFound();
  const run = await getRun(runId);
  if (!run) notFound();
  const query = await searchParams;
  const status = FILTERS.includes(query.status as (typeof FILTERS)[number]) ? (query.status as string) : "open";
  const [items, flash] = await Promise.all([exceptionQueue(runId, status), flashFrom(searchParams)]);

  return (
    <Shell staff={staff}>
      <main className="page">
        <h1>Exceptions: <Link href={`/runs/${runId}`}>run #{runId}</Link></h1>
        <Flash {...flash} />
        <p className="sub">
          {run.exceptions} exceptions, {run.exceptions_open} open. Showing:{" "}
          {FILTERS.map((f) => (
            <span key={f}>
              {f === status ? <strong>{f}</strong> : <Link href={`/runs/${runId}/queue?status=${f}`}>{f}</Link>}{" "}
            </span>
          ))}
        </p>
        {items.length === 0 ? <p className="dim">Nothing here.</p> : (
          <table className="table">
            <thead>
              <tr><th>#</th><th>Suggested reason</th><th>Status</th><th>Explanation</th><th>Resolved by</th></tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.exception_id}>
                  <td className="mono"><Link href={`/exceptions/${item.exception_id}`}>{item.ordinal}</Link></td>
                  <td className="mono">{item.suggested_reason}</td>
                  <td><StatusChip status={item.status} /></td>
                  <td>{item.explanation}</td>
                  <td>{item.resolved_by ? `${item.resolved_by} (${item.current_reason_code})` : <span className="dim">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </main>
    </Shell>
  );
}
