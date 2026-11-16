import { useState } from "react";
import { useDraft } from "./DraftContext";
import { availableVariables, type OutputField } from "./graphOps";
import type { GraphNode } from "./types";

const SHOWN = 6; // the first variables are the useful ones; the others are one click away
const typeText = (t?: string | string[]) => (Array.isArray(t) ? t.join(" ou ") : t) ?? "libre";
const TEMPLATE = /\{\{\s*([^{}]+?)\s*\}\}/g;
const ROOT = /^(?:inputs|vars|steps\.[A-Za-z0-9_-]+\.output)(?:\.[A-Za-z0-9_-]+|\[\d+\])*/;

/** Every `{{ … }}` expression found in the text values of a step, in order, without duplicates (the step is read, never changed). */
export function expressionsRead(node: GraphNode): string[] {
  const found: string[] = [];
  const walk = (v: unknown) => {
    if (typeof v === "string") for (const m of v.matchAll(TEMPLATE)) found.push(m[1] ?? "");
    else if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === "object") Object.values(v).forEach(walk);
  };
  walk(node);
  return [...new Set(found)];
}

/** The variable an expression starts from (`steps.a.output.x[0].y` → `steps.a.output.x`), when it is a known one. */
function sourceOf(expression: string, known: OutputField[]): OutputField | undefined {
  const root = ROOT.exec(expression)?.[0] ?? "";
  return known.filter((f) => root === f.path || root.startsWith(`${f.path}.`) || root.startsWith(`${f.path}[`)).sort((a, b) => b.path.length - a.path.length)[0];
}

function groupOf(f: OutputField): string {
  if (f.path.startsWith("inputs.")) return "Entrées du workflow";
  if (f.path.startsWith("vars.")) return "Variables du workflow";
  return f.label.split(" → ")[0] ?? f.label;
}

/** What a step can read: the variables it may use (workflow inputs and variables, outputs of the steps before it) with the expression to copy,
 *  and the ones it already reads. Fields of the step itself are filled in its own tab: nothing is written from here. */
export function InputsTab({ node }: { node: GraphNode }) {
  const { graph, actions } = useDraft();
  const known = availableVariables(graph, node.id, actions);
  const [copied, setCopied] = useState("");
  const [all, setAll] = useState(false);
  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(text); } catch { setCopied(""); }
  };
  const read = expressionsRead(node).map((e) => ({ e, from: sourceOf(e, known) }));
  const shown = all ? known : known.slice(0, SHOWN);
  const groups = new Map<string, OutputField[]>();
  for (const f of shown) groups.set(groupOf(f), [...(groups.get(groupOf(f)) ?? []), f]);
  const first = known[0];

  return (
    <section aria-label={`Entrées de l'étape ${node.name}`} className="outs">
      <p className="outs-help">
        Dans un champ de cette étape, écrivez une variable entre doubles accolades
        {first && <> — par exemple <code>{`{{ ${first.path} }}`}</code></>}. Elle est remplacée par sa valeur à l'exécution ; seules les variables des étapes <strong>reliées avant</strong> celle-ci sont disponibles.
      </p>
      <details className="outs-more">
        <summary>Plus d'aide</summary>
        <ul>
          <li>Dans un champ, la liste « Insérer une variable » ajoute l'expression à votre place.</li>
          <li>Pour lire un élément d'une liste, ajoutez <code>[0]</code> ; pour un champ d'un objet, ajoutez <code>.nom_du_champ</code>.</li>
          <li>Les <strong>variables</strong> du workflow sont des constantes ; les <strong>entrées</strong> sont saisies au lancement.</li>
        </ul>
      </details>

      <h3 className="outs-title">Lues par cette étape <span className="outs-count">{read.length}</span></h3>
      {read.length === 0 ? (
        <p className="hint">Cette étape ne lit encore aucune variable.</p>
      ) : (
        <ul className="outs-list" aria-label={`Variables lues par l'étape ${node.name}`}>
          {read.map(({ e, from }) => (
            <li key={e} className="outs-row">
              <code className="outs-code">{`{{ ${e} }}`}</code>
              {from ? <span className="adm-badge adm-badge-ok">{from.label}</span> : <span className="adm-badge adm-badge-warn" title="Variable inconnue ou d'une étape non reliée avant celle-ci">Non disponible ici</span>}
            </li>
          ))}
        </ul>
      )}

      <h3 className="outs-title">Variables disponibles <span className="outs-count">{known.length}</span></h3>
      {known.length === 0 ? (
        <p className="hint">Aucune variable pour l'instant : reliez une étape avant celle-ci, ou déclarez des entrées ou des variables pour le workflow.</p>
      ) : (
        <>
          {[...groups].map(([group, fields]) => (
            <ul key={group} className="outs-list" aria-label={group}>
              <li className="outs-group"><strong>{group}</strong></li>
              {fields.map((f) => {
                const text = `{{ ${f.path} }}`;
                return (
                  <li key={f.path} className="outs-row">
                    <div className="outs-line">
                      <span className="outs-name"><strong>{f.label.includes(" → ") ? f.label.split(" → ").slice(1).join(" → ") : f.label}</strong> <span className="adm-badge adm-badge-muted">{typeText(f.type)}</span></span>
                      <button type="button" className="btn-s" onClick={() => void copy(text)} aria-label={`Copier l'expression de ${f.path}`}>{copied === text ? "Copié ✓" : "Copier"}</button>
                    </div>
                    <code className="outs-code">{text}</code>
                  </li>
                );
              })}
            </ul>
          ))}
          {known.length > SHOWN && (
            <button type="button" className="btn-link" aria-expanded={all} onClick={() => setAll(!all)}>
              {all ? "Afficher moins" : `Afficher les ${known.length - SHOWN} autres variables`}
            </button>
          )}
        </>
      )}
    </section>
  );
}
