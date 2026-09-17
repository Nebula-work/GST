"use client";

import { useEffect, useState } from "react";
import { desktopBridge, type UpdateState } from "@/lib/desktop";

// Desktop only. Shows a slim strip when the Electron shell reports a newer
// GitHub Release, drives the in-app download, and offers to run the installer.
// Renders nothing on the web and until the shell has reported a state.

const SHOWN_STATES = new Set(["available", "downloading", "downloaded"]);

function percent(s: UpdateState): number | null {
  return s.total ? Math.round(((s.received ?? 0) / s.total) * 100) : null;
}

export default function UpdateBanner() {
  const [s, setS] = useState<UpdateState | null>(null);
  const [hiddenVersion, setHiddenVersion] = useState<string | null>(null); // "Not now", for this session

  useEffect(() => {
    const u = desktopBridge()?.updates;
    if (!u) return;
    const off = u.onChange(setS);
    u.state().then(setS).catch(() => {});
    return off;
  }, []);

  const u = desktopBridge()?.updates;
  if (!u || !s || !s.version || !SHOWN_STATES.has(s.state) || hiddenVersion === s.version) return null;

  const pct = percent(s);
  const isWindowsInstaller = /Setup\.exe$/i.test(s.assetName ?? "");

  return (
    <div className="update-banner" role="status">
      <span className="update-dot" aria-hidden />
      {s.state === "available" && (
        <>
          <span>
            <b>Version {s.version}</b> is available.{s.error ? ` ${s.error}` : ""}
          </span>
          <button className="update-btn" onClick={() => u.download()}>
            {s.assetUrl ? "Download update" : "Open release page"}
          </button>
          {s.notesUrl && (
            <a className="update-link" href={s.notesUrl} target="_blank" rel="noreferrer">
              What&rsquo;s new
            </a>
          )}
        </>
      )}
      {s.state === "downloading" && (
        <>
          <span>
            Downloading version {s.version}…{pct !== null ? ` ${pct}%` : ""}
          </span>
          <span className="update-bar" aria-hidden>
            <span style={{ width: `${pct ?? 0}%` }} />
          </span>
        </>
      )}
      {s.state === "downloaded" && (
        <>
          <span>
            <b>Version {s.version}</b> downloaded.{s.error ? ` ${s.error}` : ""}
          </span>
          <button className="update-btn" onClick={() => u.install()}>
            {isWindowsInstaller ? "Install now" : "Show in folder"}
          </button>
        </>
      )}
      <button className="update-close" title="Not now" onClick={() => setHiddenVersion(s.version!)}>
        ×
      </button>
    </div>
  );
}
