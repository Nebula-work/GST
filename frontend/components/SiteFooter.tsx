import Link from "next/link";
import { SUPPORT_EMAIL, SUPPORT_HREF } from "@/lib/contact";

export default function SiteFooter() {
  return (
    <footer className="foot">
      <div className="foot-left">
        <div className="foot-brand">GST Purchase Matcher</div>
        <div className="foot-sub">
          Uploaded files are processed in memory and never stored. GSTR-2A / 2B reconciliation. ·{" "}
          <Link href="/privacy" className="foot-link">
            Privacy
          </Link>
        </div>
      </div>
      <div className="foot-right">
        <span className="foot-help-label">Facing an issue? Contact me</span>
        <a className="foot-contact" href={SUPPORT_HREF}>
          ✉ {SUPPORT_EMAIL}
        </a>
      </div>
    </footer>
  );
}
