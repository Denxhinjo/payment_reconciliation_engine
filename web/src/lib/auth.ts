import { redirect } from "next/navigation";
import { NextResponse } from "next/server";
import { pool } from "@/lib/db";
import type { ErrorCode, NoticeCode } from "@/lib/messages";
import { readSessionStaffId } from "@/lib/session";

/**
 * Authorisation, enforced on the server for every route (D-077).
 *
 * Two entry points, used everywhere and nowhere else:
 * - requirePageStaff(): first line of every page. Not signed in -> redirect to /login before
 *   any query runs, so a refused response cannot carry data (the KYC status-page lesson).
 * - authorizeMutation(): first line of every POST route handler. Refuses with a short plain-text
 *   401 (not signed in), 403 (role too low) or 403 (cross-site origin). No refusal body ever
 *   echoes the resource.
 *
 * Hiding a button is presentation; these checks are the control. Roles are re-read from the
 * database on every request; the session cookie only identifies who.
 */

export type Role = "analyst" | "controller";

export interface Staff {
  id: string;
  displayName: string;
  role: Role;
}

const RANK: Record<Role, number> = { analyst: 1, controller: 2 };

export async function currentStaff(): Promise<Staff | null> {
  const staffId = await readSessionStaffId();
  if (!staffId) return null;
  const { rows } = await pool.query<{ id: string; display_name: string; role: Role }>(
    "SELECT id::text, display_name, role FROM staff_user WHERE id = $1 AND is_synthetic",
    [staffId],
  );
  const row = rows[0];
  return row ? { id: row.id, displayName: row.display_name, role: row.role } : null;
}

/** First call in every page. Redirects anonymous visitors before anything is read. */
export async function requirePageStaff(): Promise<Staff> {
  const staff = await currentStaff();
  if (!staff) redirect("/login");
  return staff;
}

function refuse(status: 401 | 403, message: string): NextResponse {
  return new NextResponse(message, {
    status,
    headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" },
  });
}

/**
 * CSRF, second layer after SameSite=Lax: a browser always sends Origin on a cross-site POST.
 * If present, it must be this site. (Non-browser clients that send no Origin still need a valid
 * session cookie.)
 */
function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return true;
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

/** First call in every POST handler. Returns the staff member, or the refusal to send. */
export async function authorizeMutation(
  request: Request,
  minimum: Role,
): Promise<Staff | NextResponse> {
  if (!sameOrigin(request)) return refuse(403, "Cross-site request refused.");
  const staff = await currentStaff();
  if (!staff) return refuse(401, "Sign in required.");
  if (RANK[staff.role] < RANK[minimum]) return refuse(403, `This action requires the ${minimum} role.`);
  return staff;
}

/**
 * 303 back to a page, carrying a message CODE (and at most a numeric reference). The page renders
 * the fixed text for that code; free text never travels in the URL (D-082).
 */
export function backTo(request: Request, path: string,
                       message: { notice?: NoticeCode; error?: ErrorCode; ref?: string }) {
  const url = new URL(path, request.url);
  if (message.notice) url.searchParams.set("notice", message.notice);
  if (message.error) url.searchParams.set("error", message.error);
  if (message.ref) url.searchParams.set("ref", message.ref);
  return NextResponse.redirect(url, 303);
}

/** Positive integer id from a path or form value, or null. */
export function parseId(value: unknown): string | null {
  return typeof value === "string" && /^[1-9][0-9]{0,17}$/.test(value) ? value : null;
}
