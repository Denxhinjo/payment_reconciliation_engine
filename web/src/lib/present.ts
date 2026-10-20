import { formatMinor } from "@/lib/format";
import type { ExceptionFigures } from "@/lib/queries";

/**
 * Fixed wording for the redesigned screens (D-096). Nothing here computes a figure: amounts and
 * counts arrive from the database views as text and are only placed into sentences.
 */

/** What each suggested reason means, in one line. */
export const REASON_SUMMARY: Record<string, string> = {
  missing_from_bank: "Settled, never reached the bank",
  amount_mismatch: "Amounts disagree across sources",
  possible_duplicate: "Same payment booked twice",
  unknown_deposit: "Bank credit with no counterpart",
  unexplained_debit: "Bank debit with no counterpart",
  missing_from_ledger: "Paid out by the processor, missing from the ledger",
  timing: "Lands after the period, each with a deadline",
};

/** How each matching pass works (design §5.4 to §5.6). */
export const PASS_SUMMARY: Record<string, { label: string; text: string }> = {
  exact: {
    label: "Exact",
    text: "A ledger bank transfer and a bank entry with the same reference and amount, within the date window",
  },
  gross_net: {
    label: "Gross / net",
    text: "One card payment paid out alone: gross in the ledger, net at the bank, and the gap equals the fee Orrery states",
  },
  many_to_one: {
    label: "Many-to-one",
    text: "Many payments paid out as one bank credit, grouped by Orrery's payout reference; all or nothing",
  },
};

const hasPayout = (f: ExceptionFigures) => f.payouts !== "0";
const lessOrMore = (f: ExceptionFigures) => (f.shortfall_minor?.startsWith("-") ? "more" : "less");

/** Short title for a queue row. */
export function exceptionTitle(f: ExceptionFigures): string {
  switch (f.suggested_reason) {
    case "missing_from_bank": return hasPayout(f) ? "Payout never reached the bank" : "Transfer never reached the bank";
    case "amount_mismatch": return `Bank received EUR ${formatMinor(f.amount_at_issue_minor)} ${lessOrMore(f)}`;
    case "possible_duplicate": return "Ledger entry booked twice";
    case "unknown_deposit": return "Deposit nobody expected";
    case "unexplained_debit": return "Debit nobody expected";
    case "missing_from_ledger": return "Payout line missing from the ledger";
    case "timing": return hasPayout(f) ? "Payout dated after period end" : "Transfer arrives after period end";
    default: return f.suggested_reason;
  }
}

/** Headline for the exception page. */
export function exceptionHeadline(f: ExceptionFigures): string {
  switch (f.suggested_reason) {
    case "amount_mismatch":
      return `The bank received EUR ${formatMinor(f.amount_at_issue_minor)} ${lessOrMore(f)} than ${hasPayout(f) ? "Orrery settled" : "the ledger expects"}`;
    case "missing_from_bank":
      return hasPayout(f) ? `Payout ${f.reference} never reached the bank` : `Transfer ${f.reference} never reached the bank`;
    case "possible_duplicate": return "A ledger entry appears to be booked twice";
    case "unknown_deposit": return "A deposit nobody expected";
    case "unexplained_debit": return "A debit nobody expected";
    case "missing_from_ledger": return "Orrery paid out a payment the ledger does not have";
    case "timing": return hasPayout(f) ? `Payout ${f.reference} lands after the period` : `Transfer ${f.reference} lands after the period`;
    default: return f.suggested_reason;
  }
}

export const KIND_LABEL: Record<string, string> = { ledger: "Ledger", settlement: "Settlement", bank: "Bank" };
export const KIND_FORMAT: Record<string, string> = { ledger: "CSV", settlement: "CSV", bank: "camt.053.001.02" };
export const ROW_NOUN: Record<string, string> = { ledger: "rows", settlement: "lines", bank: "entries" };

/** "4962e880…285567c7": enough to compare two hashes at a glance; the full value is on the run page. */
export function hashGlance(hash: string | null | undefined): string {
  return hash ? `${hash.slice(0, 8)}…${hash.slice(-8)}` : "";
}

/** "1,332": digit grouping of a count that arrives as text. */
export function grouped(count: string | null | undefined): string {
  return count ? count.replace(/\B(?=(\d{3})+(?!\d))/g, ",") : "";
}
