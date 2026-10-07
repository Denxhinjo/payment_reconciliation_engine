import { NextResponse } from "next/server";
import { backTo, parseId } from "@/lib/auth";
import { pool } from "@/lib/db";
import { sessionCookie } from "@/lib/session";

/**
 * Sign in as a synthetic staff user: the demo picker (D-031). Public by design; the accounts are
 * printed on the sign-in page. Only users marked is_synthetic can be chosen.
 */
export async function POST(request: Request) {
  let staffId: string | null = null;
  try {
    staffId = parseId((await request.formData()).get("staff_id"));
  } catch {
    staffId = null;
  }
  if (!staffId) return backTo(request, "/login", { error: "signin_choose" });
  const { rows } = await pool.query("SELECT 1 FROM staff_user WHERE id = $1 AND is_synthetic", [staffId]);
  if (rows.length === 0) return backTo(request, "/login", { error: "signin_unknown" });

  const cookie = sessionCookie(staffId);
  const response = NextResponse.redirect(new URL("/runs", request.url), 303);
  response.cookies.set(cookie.name, cookie.value, cookie.options);
  return response;
}
