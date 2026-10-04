/**
 * Display formatting ONLY (D-076). Nothing here computes a figure: every number arrives from the
 * database as text (pg returns bigint as a string) and is only rearranged for reading.
 *
 * Money is integer minor units end to end (D-001, D-072). It is never passed through Number(),
 * parseFloat() or arithmetic; the decimal point is inserted by string slicing.
 */
export function formatMinor(minor: string | null | undefined): string {
  if (minor === null || minor === undefined) return "";
  if (!/^-?[0-9]+$/.test(minor)) return minor; // never invent a number from bad input
  const negative = minor.startsWith("-");
  const digits = (negative ? minor.slice(1) : minor).padStart(3, "0");
  const whole = digits.slice(0, -2).replace(/^0+(?=\d)/, "");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}${grouped}.${digits.slice(-2)}`;
}

export function shortHash(hash: string | null | undefined): string {
  return hash ? `${hash.slice(0, 12)}…` : "";
}

/** Timestamps are shown in UTC, labelled, so two viewers never see different times for one event. */
export function formatTimestamp(value: Date | string | null | undefined): string {
  if (!value) return "";
  const date = typeof value === "string" ? new Date(value) : value;
  return `${date.toISOString().replace("T", " ").slice(0, 19)} UTC`;
}

/** DATE columns arrive as their exact 'YYYY-MM-DD' text (see the type parser in db.ts). */
export function formatDate(value: string | null | undefined): string {
  return value ?? "";
}
