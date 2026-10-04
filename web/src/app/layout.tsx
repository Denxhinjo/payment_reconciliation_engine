import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Payment Reconciliation: Demo",
  description: "Portfolio demo of a payment reconciliation engine. Synthetic data only.",
  robots: { index: false, follow: false },
};

/**
 * The root layout wraps every page, including the sign-in page and error pages, so the
 * synthetic-data banner cannot be forgotten on a page added later.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="demo-banner" role="note">
          DEMO — SYNTHETIC DATA. Orrery Payments and Demo Bank are fictional; no real people,
          accounts or transactions.
        </div>
        {children}
      </body>
    </html>
  );
}
