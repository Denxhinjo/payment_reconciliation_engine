import { recordResolution } from "@/app/api/exceptions/resolution";

/** A correction: a new resolution superseding the one the user saw (D-066). */
export async function POST(request: Request, context: { params: Promise<{ id: string }> }) {
  return recordResolution(request, (await context.params).id, true);
}
