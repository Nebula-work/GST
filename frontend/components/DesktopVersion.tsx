"use client";

import { useEffect, useState } from "react";
import { desktopBridge } from "@/lib/desktop";

// Footer tail for the desktop app: current version + a manual update check.
// Mounted-only rendering keeps the static HTML identical to what the server
// produced (no hydration mismatch); the bridge only exists at runtime.
export default function DesktopVersion() {
  const [version, setVersion] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    setVersion(desktopBridge()?.version ?? null);
  }, []);

  async function check() {
    const u = desktopBridge()?.updates;
    if (!u) return;
    setNote("Checking…");
    const s = await u.check();
    if (s.state === "latest") setNote("You have the latest version.");
    else if (s.state === "error") setNote(`Couldn't check: ${s.error}`);
    else setNote(null); // "available": the banner takes over
  }

  if (!version) return null;
  return (
    <>
      {" "}· v{version} ·{" "}
      <button className="foot-link foot-btn" onClick={check}>
        Check for updates
      </button>
      {note && <span className="foot-note"> {note}</span>}
    </>
  );
}
