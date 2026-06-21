"use client";

import { useRef, useState } from "react";

interface Props {
  num: number;
  kicker: string;
  title: string;
  hint: string;
  file: File | null;
  onPick: (file: File | null) => void;
}

function prettySize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function FileDrop({ num, kicker, title, hint, file, onPick }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  function handleFiles(list: FileList | null) {
    const f = list?.[0];
    if (f) onPick(f);
  }

  return (
    <div
      className={`drop${over ? " over" : ""}${file ? " filled" : ""}`}
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        handleFiles(e.dataTransfer.files);
      }}
    >
      <div className="kicker">
        <span className="drop-num">{num}</span>
        {kicker}
      </div>
      <h3>{title}</h3>
      <p>{hint}</p>

      {file ? (
        <div className="filemeta" onClick={(e) => e.stopPropagation()}>
          <span aria-hidden>📄</span>
          <span className="fname">{file.name}</span>
          <span className="mono muted" style={{ fontSize: 12 }}>
            {prettySize(file.size)}
          </span>
          <button
            className="clear"
            title="Remove file"
            onClick={() => {
              onPick(null);
              if (inputRef.current) inputRef.current.value = "";
            }}
          >
            ×
          </button>
        </div>
      ) : (
        <div className="placeholder">Drag &amp; drop or click to choose an .xlsx file</div>
      )}

      <input
        ref={inputRef}
        type="file"
        accept=".xlsx,.xlsm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        onChange={(e) => handleFiles(e.target.files)}
      />
    </div>
  );
}
