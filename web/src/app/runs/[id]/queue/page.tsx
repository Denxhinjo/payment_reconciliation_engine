import { notFound } from "next/navigation";
import { parseFilter, QueueView } from "@/components/queue";
import { flashFrom, Shell, type SearchParams } from "@/components/shell";
import { parseId, requirePageStaff } from "@/lib/auth";
import { getRun, runFigures } from "@/lib/queries";

export const dynamic = "force-dynamic";

/** The exceptions queue of one run. */
export default async function QueuePage({ params, searchParams }: {
  params: Promise<{ id: string }>; searchParams: SearchParams;
}) {
  const staff = await requirePageStaff();
  const runId = parseId((await params).id);
  if (!runId) notFound();
  const [run, figures] = await Promise.all([getRun(runId), runFigures(runId)]);
  if (!run || !figures) notFound();
  const [query, flash] = await Promise.all([searchParams, flashFrom(searchParams)]);
  return (
    <Shell staff={staff} active="exceptions">
      <QueueView run={run} figures={figures} filter={parseFilter(query.status)} basePath={`/runs/${runId}/queue`} flash={flash} />
    </Shell>
  );
}
