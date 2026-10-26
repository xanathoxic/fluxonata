import type { OutputField } from "./graphOps";
import type { GraphNode, ScriptOutput, VarType } from "./types";

const TYPES: [VarType, string][] = [["string", "Texte"], ["integer", "Entier"], ["number", "Nombre"], ["boolean", "Oui / Non"], ["array", "Liste"], ["object", "Objet"]];
const IDENT = /[^A-Za-z0-9_]/g;

/** "Inputs" table: each row binds a script variable to a workflow value (variable picker or literal). */
export function BindingsEditor({ node, variables, onUpdate, idPrefix = "b" }: { node: GraphNode; variables: OutputField[]; onUpdate: (p: Partial<GraphNode>) => void; idPrefix?: string }) {
  const rows = Object.entries(node.bindings ?? {});
  const write = (next: [string, unknown][]) => onUpdate({ bindings: Object.fromEntries(next) });
  return (
    <fieldset>
      <legend>Entrées du script (variables liées)</legend>
      {rows.length === 0 && <p className="hint">Aucune entrée liée. Le script peut malgré tout lire <code>inputs</code> et <code>vars</code>.</p>}
      {rows.map(([name, value], i) => (
        <div className="bind-row" key={i}>
          <label className="sr-only" htmlFor={`${idPrefix}-n-${i}`}>Nom de la variable {i + 1}</label>
          <input id={`${idPrefix}-n-${i}`} value={name} onChange={(e) => write(rows.map(([k, v], j) => (j === i ? [e.target.value.replace(IDENT, "_"), v] : [k, v])))} />
          <span aria-hidden="true">←</span>
          <label className="sr-only" htmlFor={`${idPrefix}-v-${i}`}>Valeur de la variable {name || i + 1}</label>
          <input id={`${idPrefix}-v-${i}`} value={String(value ?? "")} placeholder="{{ inputs.champ }} ou valeur" onChange={(e) => write(rows.map(([k, v], j) => (j === i ? [k, e.target.value] : [k, v])))} />
          <div className="bind-extra">
            <label className="sr-only" htmlFor={`${idPrefix}-p-${i}`}>Choisir une variable pour {name || i + 1}</label>
            <select id={`${idPrefix}-p-${i}`} value="" onChange={(e) => e.target.value && write(rows.map(([k, v], j) => (j === i ? [k, `{{ ${e.target.value} }}`] : [k, v])))}>
            <option value="">Variable…</option>
            {variables.map((v) => <option key={v.path} value={v.path}>{v.label}</option>)}
          </select>
            <button type="button" onClick={() => write(rows.filter((_, j) => j !== i))} aria-label={`Retirer la variable ${name}`}>✕</button>
          </div>
        </div>
      ))}
      <button type="button" onClick={() => write([...rows, [`entree_${rows.length + 1}`, ""]])}>Ajouter une entrée</button>
    </fieldset>
  );
}

/** "Outputs" table: values the script must define, with their type (checked after each run). */
export function OutputsEditor({ node, onUpdate, idPrefix = "o" }: { node: GraphNode; onUpdate: (p: Partial<GraphNode>) => void; idPrefix?: string }) {
  const rows = node.outputs ?? [];
  const write = (next: ScriptOutput[]) => onUpdate({ outputs: next });
  return (
    <fieldset>
      <legend>Sorties du script</legend>
      {rows.length === 0 && <p className="hint">Aucune sortie : le script ne transmet rien aux étapes suivantes.</p>}
      {rows.map((o, i) => (
        <div className="bind-row out" key={i}>
          <label className="sr-only" htmlFor={`${idPrefix}-n-${i}`}>Nom de la sortie {i + 1}</label>
          <input id={`${idPrefix}-n-${i}`} value={o.name} onChange={(e) => write(rows.map((r, j) => (j === i ? { ...r, name: e.target.value.replace(IDENT, "_") } : r)))} />
          <label className="sr-only" htmlFor={`${idPrefix}-t-${i}`}>Type de la sortie {o.name || i + 1}</label>
          <select id={`${idPrefix}-t-${i}`} value={o.type} onChange={(e) => write(rows.map((r, j) => (j === i ? { ...r, type: e.target.value as VarType } : r)))}>
            {TYPES.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <button type="button" onClick={() => write(rows.filter((_, j) => j !== i))} aria-label={`Retirer la sortie ${o.name}`}>✕</button>
        </div>
      ))}
      <button type="button" onClick={() => write([...rows, { name: `sortie_${rows.length + 1}`, type: "string" }])}>Ajouter une sortie</button>
    </fieldset>
  );
}

export { TYPES as VAR_TYPES };
