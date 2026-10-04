// `pg` is CommonJS; default import + destructure works under Next's bundler and plain Node alike.
import pg from "pg";
import type { Pool, PoolClient } from "pg";

/**
 * One shared pool, created on first use (same pattern and reasons as the KYC demo's db.ts):
 * - a pool, so each request does not pay a TCP + auth round trip;
 * - cached on globalThis, so dev hot-reloads do not leak connections;
 * - lazy, so `next build` (which imports every route) needs no DATABASE_URL.
 *
 * The connection string must belong to a login role that is a member of `recon_web`. That role
 * can SELECT and INSERT but never UPDATE or DELETE; the only other write it has is EXECUTE on
 * requeue_failed_job(). So the database, not this app, is what stops the UI rewriting history
 * (D-019, D-074).
 */
declare global {
  // eslint-disable-next-line no-var
  var __reconPool: Pool | undefined;
}

// DATE (OID 1082) as the exact text the database holds. pg's default turns it into a JavaScript
// Date at *local* midnight, which shifts business dates by a day depending on the server's time
// zone, the same mistake D-026 avoids in the engine. bigint already arrives as text.
pg.types.setTypeParser(1082, (value: string) => value);

function sslOptions(connectionString: string) {
  // Local containers get a plain connection; anything remote (e.g. Neon) requires TLS.
  const isLocal = /@(localhost|127\.0\.0\.1|db)[:/]/.test(connectionString);
  return isLocal ? {} : { ssl: { rejectUnauthorized: true } };
}

function getPool(): Pool {
  if (globalThis.__reconPool) return globalThis.__reconPool;
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) {
    throw new Error("DATABASE_URL is not set (a login role that is a member of recon_web).");
  }
  const created = new pg.Pool({
    connectionString,
    application_name: "recon_web",
    max: 10,
    connectionTimeoutMillis: 5_000,
    ...sslOptions(connectionString),
  });
  globalThis.__reconPool = created;
  return created;
}

export const pool: Pool = new Proxy({} as Pool, {
  get(_target, property) {
    const actual = getPool();
    const value = Reflect.get(actual, property, actual);
    return typeof value === "function" ? value.bind(actual) : value;
  },
});

/** Everything the callback does on `client` commits together or rolls back together. */
export async function withTransaction<T>(fn: (client: PoolClient) => Promise<T>): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query("begin");
    const result = await fn(client);
    await client.query("commit");
    return result;
  } catch (err) {
    await client.query("rollback").catch(() => undefined);
    throw err;
  } finally {
    client.release();
  }
}

/** A Postgres error as pg reports it: SQLSTATE in `code`, the violated constraint by name. */
export interface PgError {
  code?: string;
  constraint?: string;
  message: string;
}

export function asPgError(err: unknown): PgError | null {
  if (err && typeof err === "object" && "message" in err) return err as PgError;
  return null;
}
