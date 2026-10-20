"use client";

import { useEffect, useState } from "react";

/**
 * The only client-side code: conveniences while filling a form. Every rule they hint at is
 * enforced again on the server and in the database; turning JavaScript off loses only the hints.
 */

const KINDS = [
  { value: "ledger", label: "Ledger", hint: "CSV", drop: "ledger CSV", accept: ".csv", source: "Internal ledger export · .csv" },
  { value: "settlement", label: "Settlement", hint: "Orrery CSV", drop: "Orrery settlement CSV", accept: ".csv", source: "Orrery settlement report · .csv" },
  { value: "bank", label: "Bank statement", hint: "camt.053 XML", drop: "camt.053 statement", accept: ".xml", source: "Demo Bank statement · camt.053.001.02 · .xml" },
] as const;

export function UploadPicker() {
  const [kind, setKind] = useState<(typeof KINDS)[number]>(KINDS[0]);
  const [file, setFile] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  return (
    <>
      <span className="field-label">What kind of file?</span>
      <div className="kinds" role="radiogroup" aria-label="Kind of file" style={{ marginTop: "var(--space-2)" }}>
        {KINDS.map((k) => (
          <label key={k.value}>
            <input type="radio" name="kind" value={k.value} required defaultChecked={k.value === "ledger"}
                   onChange={() => setKind(k)} />
            <strong>{k.label}</strong>
            <small>{k.hint}</small>
          </label>
        ))}
      </div>
      <label className={`dropzone${over ? " over" : ""}`}
             onDragEnter={() => setOver(true)} onDragLeave={() => setOver(false)} onDrop={() => setOver(false)}>
        <input type="file" name="file" required accept={kind.accept}
               onChange={(e) => setFile(e.currentTarget.files?.[0]?.name ?? null)} />
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
             strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M12 15V4M7.5 8.5L12 4l4.5 4.5M5 15v3a2 2 0 002 2h10a2 2 0 002-2v-3" />
        </svg>
        <div className="drop-title">Drop your {kind.drop} here, or <u>browse</u></div>
        <div className="drop-hint">{kind.source}</div>
        {file ? <div className="chosen">{file}</div> : null}
      </label>
      <button type="submit" className="button primary block">Upload {kind.label.toLowerCase()}</button>
    </>
  );
}

/** Note with a live count of non-blank characters (the database requires at least 10, D-020). */
export function NoteField({ submitLabel }: { submitLabel: string }) {
  const [count, setCount] = useState(0);
  const [live, setLive] = useState(false);       // false until hydrated: without JS the button works
  useEffect(() => setLive(true), []);
  const enough = count >= 10;
  return (
    <>
      <label className="field">
        <span>Note</span>
        <textarea name="note" required minLength={10} rows={4}
                  placeholder="e.g. Claim raised with Orrery for the shortfall."
                  onChange={(e) => setCount(e.currentTarget.value.replace(/\s/g, "").length)} />
        <span className="help">{count} / 10 characters minimum</span>
      </label>
      <button type="submit" className="button primary block" disabled={live && !enough}>{submitLabel}</button>
    </>
  );
}
