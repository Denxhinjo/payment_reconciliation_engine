import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import "./globals.css";

// Self-hosted by Next.js at build time: the browser never requests a font from a third party.
const plexSans = IBM_Plex_Sans({
  subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--font-plex-sans", display: "swap",
});
const plexMono = IBM_Plex_Mono({
  subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-plex-mono", display: "swap",
});

export const metadata: Metadata = {
  title: "Reconcile: payment reconciliation demo",
  description: "Portfolio demo of a payment reconciliation engine. Synthetic data only.",
  robots: { index: false, follow: false },
};

/**
 * The root layout wraps every page, including the sign-in page and error pages, so the
 * synthetic-data banner cannot be forgotten on a page added later. The reset sentence states the
 * schedule GitHub actually keeps, not the one it is asked for (D-093, D-095).
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${plexSans.variable} ${plexMono.variable}`}>
      <body>
        <div className="demo-banner" role="note">
          <span className="demo-mark">DEMO</span><span className="sep">·</span>
          Synthetic data. Orrery Payments and Demo Bank are fictional — no real people, accounts or
          transactions.{" "}
          <span className="demo-reset">
            Resets once a day, scheduled for 03:00 UTC but often hours later; anything you change is discarded then.
          </span>
        </div>
        {children}
      </body>
    </html>
  );
}
