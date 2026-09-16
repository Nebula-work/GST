import type { Metadata } from "next";
import { Fraunces, Hanken_Grotesk, IBM_Plex_Mono } from "next/font/google";
import Script from "next/script";
import { IS_DESKTOP } from "@/lib/desktop";
import "./globals.css";

const display = Fraunces({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-display",
  display: "swap",
});

const sans = Hanken_Grotesk({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans",
  display: "swap",
});

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "GST Reconciliation — Purchase ⇄ GSTR-2A/2B",
  description:
    "Match your purchase register against the GST portal (GSTR-2A/2B) and find every mismatch.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={`${display.variable} ${sans.variable} ${mono.variable}`}>
        {children}
        {/* Hosted-site analytics only: the desktop app makes no network calls. */}
        {!IS_DESKTOP && (
          <Script
            defer
            src="https://static.cloudflareinsights.com/beacon.min.js"
            data-cf-beacon='{"token": "e6ca49dcfe064cb7a88a41b4f571b813"}'
            strategy="afterInteractive"
          />
        )}
      </body>
    </html>
  );
}
