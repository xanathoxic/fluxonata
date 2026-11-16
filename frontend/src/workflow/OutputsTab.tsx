import { Fragment, useState } from "react";
import type { WorkflowDetail } from "../api";
import { useWsQuery } from "../lib";
import { useDraft } from "./DraftContext";
import { nodeOutputFields, type OutputField } from "./graphOps";
import type { GraphNode } from "./types";

const SHOWN = 6; // the first variables are the useful ones; the others are one click away
const typeText = (t?: string | string[]) => (Array.isArray(t) ? t.join(" ou ") : t) ?? "libre";
const isFree = (v: string | undefined) => (v ?? "").trim() === "";
const expr = (path: string) => `{{ ${path} }}`;
const keyOf = (f: OutputField, id: string) => f.path.slice(`steps.${id}.output.`.length);
const NAME = /^[a-z][a-z0-9_]*$/;

/** How a field reads in another step: a list gets its first element, an object one of its keys, anything else is used as is. */
export function usageExample(f: OutputField): string {
  const t = Array.isArray(f.type) ? f.type[0] : f.type;
  if (t === "array") return `{{ ${f.path}[0] }}`;
  if (t === "object") return `{{ ${f.path}.nom_du_champ }}`;
  return expr(f.path);
}

/** Why a step shows no output variable (and what to do about it, when there is something to do). */
function emptyReason(node: GraphNode, childLoaded: boolean): string {
  if (node.type === "script") return "Cette étape n'a pas de variable de sortie : déclarez-les dans l'onglet Entrées/Sorties.";
  if (node.type === "action") return "Cette étape n'a pas de variable de sortie (l'action ne déclare aucune sortie).";
  if (node.type === "subworkflow") return !node.workflow ? "Choisissez d'abord le workflow à appeler : ses sorties deviennent celles de cette étape." : childLoaded ? "Le workflow appelé ne déclare aucune sortie (onglet Entrées/Sorties de ce workflow)." : "Chargement des sorties du workflow appelé…";
  if (node.type === "wait") return "Une minuterie n'a pas de variable de sortie ; une attente reprise par un appel ou un formulaire renvoie les données reçues.";
  if (node.type === "throw") return "Une exception n'a pas de variable de sortie : elle arrête l'étape en erreur.";
  return "Cette étape n'a pas de variable de sortie.";
}

/** An expression that may break after each dot (a narrow panel) without anything being added to the text that is copied. */
function Breakable({ text }: { text: string }) {
  let path = ""; // each key is the text up to that part: unique, and stable for a given expression
  return (
    <>
      {text.split(".").map((part) => {
        const first = path === "";
        path = first ? part : `${path}.${part}`;
        return <Fragment key={path}>{!first && <>.<wbr /></>}{part}</Fragment>;
      })}
    </>
  );
}

/** The output variables of a step, one line each: its name and type, and the expression that reads it (copy in one click). Linking a variable to a
 *  workflow output is folded away until asked for. Workflow variables are constants: an output cannot be assigned to one. */
export function OutputsTab({ node }: { node: GraphNode }) {
  const { graph, setGraph, actions, editable } = useDraft();
  // a sub-workflow step returns the outputs of the workflow it calls: read from that workflow's draft
  const child = useWsQuery<WorkflowDetail>(`workflow-${node.workflow ?? ""}`, `/workflows/${node.workflow ?? ""}/`, { enabled: node.type === "subworkflow" && !!node.workflow });
  const childFields: OutputField[] = Object.keys(child.data?.draft_graph?.outputs ?? {}).map((k) => ({ path: `steps.${node.id}.output.${k}`, label: `${node.name} → ${k}` }));
  const fields = node.type === "subworkflow" ? childFields : nodeOutputFields(node, actions);
  const outputs = graph.outputs ?? {};
  const [copied, setCopied] = useState("");
  const [open, setOpen] = useState("");
  const [all, setAll] = useState(false);
  const [newName, setNewName] = useState<Record<string, string>>({});
  const linkedTo = (path: string) => Object.entries(outputs).filter(([, v]) => v.trim() === expr(path)).map(([k]) => k);
  const setOutputs = (next: Record<string, string>) => setGraph({ ...graph, outputs: next });
  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(text); } catch { setCopied(""); }
  };
  const shown = all ? fields : fields.slice(0, SHOWN);
  const first = fields[0];
  const label = (f: OutputField) => f.label.replace(`${node.name} → `, "");

  return (
    <section aria-label={`Sorties de l'étape ${node.name}`} className="outs">
      <p className="outs-help">
        Reliez cette étape <strong>avant</strong> celle qui doit lire ses résultats, puis copiez l'expression d'une variable dans un champ de cette étape
        {first && <> — par exemple <code>{usageExample(first)}</code></>}.
        {node.type === "subworkflow" && fields.length > 0 && <> Ces variables sont les sorties du workflow appelé (d'après son brouillon).</>}
      </p>
      <details className="outs-more">
        <summary>Plus d'aide</summary>
        <ul>
          <li>Dans le champ, vous pouvez aussi choisir la variable dans la liste « Insérer une variable ».</li>
          <li>Une étape qui échoue n'a pas de sortie, sauf les commandes SSH : elles gardent leurs résultats partiels pour la branche « En cas d'erreur ».</li>
          <li>Lier une variable à une <strong>sortie du workflow</strong> la renvoie à la fin du workflow (onglet Entrées/Sorties du workflow). Les <strong>variables</strong> du workflow sont des constantes : une sortie d'étape ne peut pas leur être affectée.</li>
        </ul>
      </details>

      {fields.length === 0 ? (
        <p className="hint">{emptyReason(node, !!child.data)}</p>
      ) : (
        <>
          <h3 className="outs-title">Variables de sortie <span className="outs-count">{fields.length}</span></h3>
          <ul className="outs-list" aria-label={`Variables de sortie de l'étape ${node.name}`}>
            {shown.map((f) => {
              const key = keyOf(f, node.id);
              const linked = linkedTo(f.path);
              const id = `${node.id}-${key}`.replace(/[^A-Za-z0-9_-]/g, "_");
              const isOpen = open === f.path;
              const free = Object.entries(outputs).filter(([k, v]) => isFree(v) && !linked.includes(k)).map(([k]) => k);
              const suggestion = newName[f.path] ?? `${node.id}_${key}`.replace(/[^a-z0-9_]/gi, "_").toLowerCase();
              const taken = suggestion in outputs && !linked.includes(suggestion);
              const example = usageExample(f);
              return (
                <li key={f.path} className="outs-row">
                  <div className="outs-line">
                    <span className="outs-name"><strong>{label(f)}</strong> <span className="adm-badge adm-badge-muted">{typeText(f.type)}</span></span>
                    <button type="button" className="btn-s" onClick={() => void copy(example)} aria-label={`Copier l'expression de ${key}`}>{copied === example ? "Copié ✓" : "Copier"}</button>
                  </div>
                  <code className="outs-code"><Breakable text={example} /></code>
                  <div className="outs-link">
                    {linked.map((k) => <span key={k} className="adm-badge adm-badge-ok" title="Sortie du workflow liée">↦ {k}</span>)}
                    {editable && (
                      <button type="button" className="outs-toggle" aria-expanded={isOpen} aria-controls={`link-${id}`} aria-label={`Lier ${key} à une sortie du workflow`} onClick={() => setOpen(isOpen ? "" : f.path)}>
                        {linked.length > 0 ? "Modifier le lien" : "Lier au workflow…"}
                      </button>
                    )}
                  </div>
                  {editable && isOpen && (
                    <div id={`link-${id}`} className="outs-panel">
                      {free.length > 0 && (
                        <div className="field">
                          <label htmlFor={`sel-${id}`}>Sortie du workflow existante (encore vide)</label>
                          <select id={`sel-${id}`} value="" onChange={(e) => { if (e.target.value) { setOutputs({ ...outputs, [e.target.value]: expr(f.path) }); setOpen(""); } }}>
                            <option value="">Choisir…</option>{free.map((k) => <option key={k} value={k}>{k}</option>)}
                          </select>
                        </div>
                      )}
                      <div className="field">
                        <label htmlFor={`new-${id}`}>{free.length > 0 ? "…ou nouvelle sortie" : "Nouvelle sortie du workflow"}</label>
                        <div className="inline-row">
                          <input id={`new-${id}`} value={suggestion} pattern="[a-z][a-z0-9_]*" onChange={(e) => setNewName({ ...newName, [f.path]: e.target.value })} />
                          <button type="button" className="btn-s primary" disabled={!NAME.test(suggestion) || taken} aria-label={`Créer la sortie ${suggestion} liée à ${key}`}
                            onClick={() => { setOutputs({ ...outputs, [suggestion]: expr(f.path) }); setOpen(""); }}>Créer et lier</button>
                        </div>
                        {taken && <p className="hint" role="status">Une sortie « {suggestion} » existe déjà : choisissez un autre nom.</p>}
                      </div>
                      {linked.map((k) => (
                        <button key={k} type="button" className="btn-link danger-link" aria-label={`Retirer la sortie ${k} du workflow`}
                          onClick={() => { setOutputs(Object.fromEntries(Object.entries(outputs).filter(([kk]) => kk !== k))); setOpen(""); }}>Retirer « {k} » du workflow</button>
                      ))}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          {fields.length > SHOWN && (
            <button type="button" className="btn-link" aria-expanded={all} onClick={() => setAll(!all)}>
              {all ? "Afficher moins" : `Afficher les ${fields.length - SHOWN} autres variables`}
            </button>
          )}
        </>
      )}
    </section>
  );
}
