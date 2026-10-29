import { useMutation } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { api, type ApiError, type JsonSchema } from "../api";
import { useApp } from "../context";
import { errorText } from "../lib";
import { Badge } from "./AdminUi";
import { AiNotice, useAiMode } from "./AiBox";

export interface Proposal<P> { proposal: P; valid: boolean; problems: Record<string, unknown>; warnings: string[]; repaired: boolean; notice: string }

/** What the platform found wrong with a proposal, in words. */
function problemLines(problems: Record<string, unknown>): string[] {
  return Object.entries(problems).flatMap(([field, v]) => (Array.isArray(v) ? v : [v]).map((m) => `${field} : ${typeof m === "string" ? m : JSON.stringify(m)}`));
}

/** A flat table of the fields of a (JSON Schema) object: name, type, required, what it is for. */
export function SchemaTable({ schema, caption }: { schema: JsonSchema | null | undefined; caption: string }) {
  const required = new Set(schema?.required ?? []);
  const rows = Object.entries(schema?.properties ?? {});
  return (
    <div className="table-wrap"><table className="adm-table"><caption className="sr-only">{caption}</caption>
      <thead><tr><th scope="col">Nom</th><th scope="col">Type</th><th scope="col">Obligatoire</th><th scope="col">À quoi ça sert</th></tr></thead>
      <tbody>{rows.length === 0 ? <tr><td colSpan={4}>{schema?.description || "Aucun champ."}</td></tr> : rows.map(([name, p]) => (
        <tr key={name}><th scope="row" className="mono">{name}</th><td>{Array.isArray(p.type) ? p.type.join(" | ") : p.type}</td><td>{required.has(name) ? "Oui" : "Non"}</td><td>{p.title ? <strong>{p.title}. </strong> : null}{p.description}</td></tr>))}
      </tbody></table></div>
  );
}

/**
 * Shared shell of the creation assistants (actions, AI skills): describe the need, read the proposal, refine it with an instruction,
 * then hand it to the usual creation form where a person reviews it. Nothing is saved here.
 */
export function AssistantPanel<P>({ endpoint, extra, placeholder, examples, render, onUse, onClose, what }: {
  endpoint: string; extra?: Record<string, unknown>; placeholder: string; examples: string[]; what: string;
  render: (p: P) => ReactNode; onUse: (p: P) => void; onClose: () => void;
}) {
  const { workspace } = useApp();
  const ai = useAiMode();
  const [need, setNeed] = useState("");
  const [instruction, setInstruction] = useState("");
  const [result, setResult] = useState<Proposal<P> | null>(null);
  const ask = useMutation<Proposal<P>, ApiError, { revise: boolean }>({
    mutationFn: ({ revise }) => api<Proposal<P>>(`/workspaces/${workspace.slug}${endpoint}`, { json: { need, ...extra, ...(revise && result ? { previous: result.proposal, instruction } : {}) } }),
    onSuccess: (r, v) => { setResult(r); if (v.revise) setInstruction(""); },
  });
  const issues = result ? problemLines(result.problems) : [];
  return (
    <div className="asst">
      <AiNotice mode={ai.mode} />
      <div className="field">
        <label htmlFor="asst-need">Décrivez ce dont vous avez besoin</label>
        <textarea id="asst-need" rows={4} maxLength={1500} value={need} onChange={(e) => setNeed(e.target.value)} placeholder={placeholder} aria-describedby="asst-need-h" />
        <p id="asst-need-h" className="hint">{need.length} / 1500 caractères. Deux ou trois phrases suffisent : ce que {what} doit recevoir, ce qu'il doit produire, les règles à respecter.</p>
      </div>
      {!result && examples.length > 0 && (
        <div className="asst-ex"><span className="hint">Exemples :</span>{examples.map((e) => <button type="button" key={e} className="chip-btn" onClick={() => setNeed(e)}>{e}</button>)}</div>
      )}
      <div className="actions">
        <button type="button" className="primary" disabled={!need.trim() || ask.isPending} onClick={() => ask.mutate({ revise: false })}>{ask.isPending && !ask.variables?.revise ? "Réflexion…" : result ? "Proposer à nouveau (repartir de zéro)" : "Proposer"}</button>
        <button type="button" onClick={onClose}>Annuler</button>
      </div>
      {ask.error && <p role="alert" className="field-error">Erreur : {errorText(ask.error)}</p>}
      {result && (
        <section className="asst-result" aria-label="Proposition de l'assistant" aria-live="polite">
          <div className="asst-state">
            {result.valid ? <Badge tone="ok">✔ Contrôles de la plateforme réussis</Badge> : <Badge tone="warn">⚠ Problèmes à corriger</Badge>}
            {result.repaired && <Badge tone="info">Corrigée automatiquement une fois</Badge>}
          </div>
          {issues.length > 0 && <div role="alert" className="field-error">La plateforme a relevé :<ul>{issues.map((m) => <li key={m}>{m}</li>)}</ul></div>}
          {result.warnings.length > 0 && <div className="notice"><strong>À savoir</strong><ul>{result.warnings.map((m) => <li key={m}>{m}</li>)}</ul></div>}
          {render(result.proposal)}
          <p className="hint">{result.notice}</p>
          <div className="field">
            <label htmlFor="asst-instr">Ajuster la proposition</label>
            <div className="inline-form">
              <input id="asst-instr" className="grow" value={instruction} maxLength={1000} onChange={(e) => setInstruction(e.target.value)} placeholder="ex. Ajoute un paramètre « séparateur », renvoie aussi le nombre de lignes…" />
              <button type="button" disabled={!instruction.trim() || ask.isPending} onClick={() => ask.mutate({ revise: true })}>{ask.isPending && ask.variables?.revise ? "Réflexion…" : "Réviser"}</button>
            </div>
          </div>
          <div className="actions">
            <button type="button" className="primary" onClick={() => onUse(result.proposal)}>Utiliser cette proposition</button>
            <span className="hint">Elle s'ouvre dans le formulaire de création : vous la relisez, la modifiez et la publiez vous-même.</span>
          </div>
        </section>
      )}
    </div>
  );
}
