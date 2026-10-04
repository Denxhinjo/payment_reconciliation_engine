import { recordResolution } from "@/app/api/exceptions/resolution";

/** First resolution of an exception (supersedes nothing). */
export async function POST(request: Request, context: { params: Promise<{ id: string }> }) {
  return recordResolution(request, (await context.params).id, false);
}
