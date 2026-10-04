import crypto from "node:crypto";
import { cookies } from "next/headers";

/**
 * Demo sessions: a signed cookie naming a synthetic staff user (D-031, D-073).
 *
 * Signed, not encrypted: signing stops forgery, and the payload (a staff id and two timestamps)
 * is not secret. There is no password: this is the "sign in as" picker over synthetic staff,
 * and the accounts are printed on the sign-in page. Known fragility F6: anyone can act as anyone.
 * Real users would need real authentication, revocation and separation of duties.
 *
 * The cookie only says WHO. The role is re-read from staff_user on every request (auth.ts), so
 * a role is never trusted from the client.
 */

const COOKIE_NAME = "recon_session";
const SESSION_HOURS = 8;

interface SessionPayload {
  sid: string;      // staff_user.id
  iat: number;
  exp: number;
}

function secret(): string {
  const value = process.env.SESSION_SECRET;
  if (!value || value.length < 32) {
    throw new Error("SESSION_SECRET is not set or shorter than 32 characters.");
  }
  return value;
}

function sign(payload: string): string {
  return crypto.createHmac("sha256", secret()).update(payload).digest("base64url");
}

function constantTimeEquals(a: string, b: string): boolean {
  const left = Buffer.from(a, "utf8");
  const right = Buffer.from(b, "utf8");
  if (left.length !== right.length) return false;
  return crypto.timingSafeEqual(left, right);
}

function decode(value: string): SessionPayload | null {
  const [payload, signature] = value.split(".");
  if (!payload || !signature) return null;
  // Signature first: nothing in an unverified payload is read.
  if (!constantTimeEquals(signature, sign(payload))) return null;
  try {
    const session = JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as SessionPayload;
    if (!/^[0-9]+$/.test(session.sid) || session.exp < Date.now()) return null;
    return session;
  } catch {
    return null;
  }
}

export async function readSessionStaffId(): Promise<string | null> {
  const store = await cookies();
  const raw = store.get(COOKIE_NAME)?.value;
  return raw ? decode(raw)?.sid ?? null : null;
}

export function sessionCookie(staffId: string): { name: string; value: string; options: object } {
  const now = Date.now();
  const payload = Buffer.from(
    JSON.stringify({ sid: staffId, iat: now, exp: now + SESSION_HOURS * 3_600_000 }),
    "utf8",
  ).toString("base64url");
  return {
    name: COOKIE_NAME,
    value: `${payload}.${sign(payload)}`,
    options: {
      httpOnly: true,          // not readable by page scripts
      sameSite: "lax" as const, // not sent on cross-site POSTs: the first CSRF defence
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: SESSION_HOURS * 3600,
    },
  };
}

export const SESSION_COOKIE_NAME = COOKIE_NAME;
