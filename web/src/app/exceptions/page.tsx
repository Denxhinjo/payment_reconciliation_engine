import Link from "next/link";
import { parseFilter, QueueView } from "@/components/queue";
import { flashFrom, Shell, type SearchParams } from "@/components/shell";
import { requirePageStaff } from "@/lib/auth";
import { getRun, latestRunId, runFigures } from "@/lib/queries";

export const dynamic = "force-dynamic";

/** The exceptions queue of the run the dashboard opens on: the newest finished original run. */
export default async function ExceptionsPage({ searchParams }: { searchParams: SearchParams }) {
  const staff = await requirePageStaff();
  const latest = await latestRunId();
  const [run, figures] = latest ? await Promise.all([getRun(latest), runFigures(latest)]) : [null, null];
  const [query, flash] = await Promise.all([searchParams, flashFrom(searchParams)]);
  return (
    <Shell staff={staff} active="exceptions">
      {run && figures ? (
        <QueueView run={run} figures={figures} filter={parseFilter(query.status)} basePath="/exceptions" flash={flash} />
      ) : (
        <main className="page">
          <h1>Exceptions queue</h1>
          <p className="lead">No finished run yet. <Link href="/upload">Upload the three files</Link> and request a reconciliation.</p>
        </main>
      )}
    </Shell>
  );
}
