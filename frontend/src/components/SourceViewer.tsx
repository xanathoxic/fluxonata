import { useState } from "react";
import { Badge } from "./AdminUi";
import { CodeEditor } from "./CodeEditor";

const LANGUAGE: Record<string, { label: string; editor: "python" | "javascript" | "text" }> = {
  python: { label: "Python", editor: "python" },
  node: { label: "Node.js", editor: "javascript" },
  typescript: { label: "TypeScript", editor: "javascript" },
  shell: { label: "Shell", editor: "text" },
  powershell: { label: "PowerShell", editor: "text" },
};

/** Code d'une version en lecture seule (éditeur de code : numéros de ligne, coloration, recherche au clavier), avec barre d'outils. */
export function SourceViewer({ source, runtime, version, digest, name }: { source: string; runtime: string; version: string; digest: string; name: string }) {
  const lang = LANGUAGE[runtime] ?? { label: runtime, editor: "text" as const };
  const lines = source.split("\n").length;
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard?.writeText(source).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 2000); }, () => undefined);
  };
  return (
    <section className="src-view" aria-label={`Code de la version ${version}`}>
      <div className="src-bar">
        <span className="src-file">{name}</span>
        <Badge tone="info">{lang.label}</Badge>
        <span className="src-meta">v{version} · {lines} ligne{lines > 1 ? "s" : ""} · empreinte <span className="mono">{digest.slice(7, 19)}…</span></span>
        <span className="src-ro">Lecture seule</span>
        <button type="button" onClick={copy}>{copied ? "Copié ✓" : "Copier le code"}</button>
      </div>
      <CodeEditor value={source} onChange={() => undefined} language={lang.editor} readOnly ariaLabel={`Code source de la version ${version} (lecture seule)`} minHeight="320px" />
    </section>
  );
}
