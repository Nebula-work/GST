import type { Metadata } from "next";
import Link from "next/link";
import SiteFooter from "@/components/SiteFooter";
import { OWNER_NAME, SUPPORT_EMAIL, SUPPORT_HREF } from "@/lib/contact";

export const metadata: Metadata = {
  title: "Privacy Policy — GST Purchase Matcher",
  description:
    "How the GST Purchase Matcher handles the files and data you upload.",
};

const LAST_UPDATED = "21 June 2026";

export default function PrivacyPage() {
  return (
    <main className="shell">
      <header className="page-head">
        <Link href="/" className="back-link">
          ← Back to the tool
        </Link>
        <h1 className="page-title">Privacy Policy</h1>
        <p className="page-meta">
          GST Purchase Matcher · Last updated {LAST_UPDATED}
        </p>
      </header>

      <article className="panel legal">
        <p className="lede">
          This policy explains what happens to the data you provide when you use
          the GST Purchase Matcher (the “tool”) to reconcile your purchase
          register against the GST portal. In short:{" "}
          <strong>
            we use your files only to produce your report, and we do not store
            them.
          </strong>
        </p>

        <h2>Who operates this tool</h2>
        <p>
          This tool is operated by {OWNER_NAME}. For any question about this
          policy or your data, email{" "}
          <a href={SUPPORT_HREF}>{SUPPORT_EMAIL}</a>.
        </p>

        <h2>What we receive</h2>
        <p>
          To generate a reconciliation report you upload two Excel files — your
          GST portal export (GSTR-2A / 2B) and your purchase register. These
          files may contain GSTINs, supplier and vendor names, invoice numbers
          and dates, taxable values, and tax amounts (IGST / CGST / SGST / cess).
        </p>
        <p>
          We receive only what is inside the files you choose to upload. We do
          not ask for or collect account names, passwords, phone numbers, or
          payment details, and the tool has no login.
        </p>

        <h2>How we use it</h2>
        <p>
          Your files are sent to the server, parsed and compared{" "}
          <strong>in memory</strong> to produce the on-screen report (matched
          invoices, mismatches, unclaimed ITC and ITC at risk), and the report
          is returned to your browser. The data is used for this reconciliation
          and nothing else.
        </p>

        <h2>We do not store your files</h2>
        <p>
          Your uploaded files and their contents are processed in memory and
          discarded as soon as your report is returned. We do not save them to a
          database, we do not write them to disk, and we keep no copies. Any CSV
          export is generated inside your own browser.
        </p>
        <p>
          For security and reliability, the server may keep standard, short-lived
          technical logs (such as timestamps, IP address and error diagnostics).
          These logs do not contain the contents of your files.
        </p>

        <h2>No sharing, no tracking</h2>
        <p>
          We do not sell your data and do not share it with any third party. The
          tool uses no advertising, no analytics, and no tracking cookies.
        </p>

        <h2>Security</h2>
        <p>
          Your data is processed only transiently during reconciliation, and
          when the tool is served over HTTPS your uploads are encrypted in
          transit. No method of transmission or processing is completely secure,
          but because we do not retain your files there is no stored dataset to
          be exposed.
        </p>

        <h2>Your rights</h2>
        <p>
          Because we do not retain your files, there is no stored personal data
          for us to access, correct, or delete on request — you decide what to
          upload, and you can stop using the tool at any time. Where applicable
          laws (such as India’s Digital Personal Data Protection Act, 2023) grant
          you rights over your personal data, you can reach us at the email
          below.
        </p>

        <h2>Changes to this policy</h2>
        <p>
          We may update this policy from time to time. The “Last updated” date at
          the top reflects the current version.
        </p>

        <h2>Contact</h2>
        <p>
          Questions about this policy or your data? Email{" "}
          <a href={SUPPORT_HREF}>{SUPPORT_EMAIL}</a>.
        </p>

        <p className="legal-note">
          This tool assists with reconciliation and does not provide tax advice;
          always review results before filing.
        </p>
      </article>

      <SiteFooter />
    </main>
  );
}
