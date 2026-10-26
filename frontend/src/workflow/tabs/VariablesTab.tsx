import { useDraft } from "../DraftContext";
import { VAR_TYPES } from "../ScriptParts";
import type { VarType, Variable } from "../types";

function ValueInput({ v, onChange, id }: { v: Variable; onChange: (val: unknown) => void; id: string }) {
  if (v.type === "boolean") return <select id={id} value={String(v.value === true)} onChange={(e) => onChange(e.target.value === "true")}><option value="false">Non</option><option value="true">Oui</option></select>;
  if (v.type === "integer" || v.type === "number") return <input id={id} type="number" value={typeof v.value === "number" ? v.value : ""} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />;
  if (v.type === "array" || v.type === "object") return <input id={id} className="mono" defaultValue={JSON.stringify(v.value ?? (v.type === "array" ? [] : {}))} onBlur={(e) => { try { onChange(JSON.parse(e.target.value)); } catch { /* keep previous value until valid JSON */ } }} aria-describedby={`${id}-h`} />;
  return <input id={id} value={typeof v.value === "string" ? v.value : ""} onChange={(e) => onChange(e.target.value)} />;
}

/** Workflow variables : named constants readable everywhere as vars.<name> and in scripts. */
export function VariablesTab() {
  const { graph, setGraph, editable } = useDraft();
  const vars = graph.variables ?? [];
  const write = (next: Variable[]) => setGraph({ ...graph, variables: next });
  const patch = (i: number, p: Partial<Variable>) => write(vars.map((v, j) => (j === i ? { ...v, ...p } : v)));
  return (
    <section aria-label="Variables du workflow">
      <h2>Variables</h2>
      <p className="hint">Constantes du workflow : lisibles dans les expressions (<code>{"{{ vars.nom }}"}</code>) et dans les scripts (<code>vars["nom"]</code>). Elles ne contiennent jamais de secret : utilisez une connexion.</p>
      <fieldset disabled={!editable} className="bare">
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Variables du workflow</caption>
            <thead><tr><th scope="col">Nom</th><th scope="col">Type</th><th scope="col">Valeur</th><th scope="col">Description</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {vars.map((v, i) => (
                <tr key={i}>
                  <td><label className="sr-only" htmlFor={`v-n-${i}`}>Nom de la variable {i + 1}</label><input id={`v-n-${i}`} value={v.name} pattern="[A-Za-z_][A-Za-z0-9_]*" onChange={(e) => patch(i, { name: e.target.value.replace(/[^A-Za-z0-9_]/g, "_") })} /></td>
                  <td><label className="sr-only" htmlFor={`v-t-${i}`}>Type de {v.name}</label>
                    <select id={`v-t-${i}`} value={v.type} onChange={(e) => patch(i, { type: e.target.value as VarType, value: e.target.value === "boolean" ? false : e.target.value === "array" ? [] : e.target.value === "object" ? {} : null })}>
                      {VAR_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
                  <td><label className="sr-only" htmlFor={`v-v-${i}`}>Valeur de {v.name}</label><ValueInput id={`v-v-${i}`} v={v} onChange={(val) => patch(i, { value: val })} />
                    {(v.type === "array" || v.type === "object") && <span id={`v-v-${i}-h`} className="sr-only">Saisissez du JSON valide ; la valeur est enregistrée en quittant le champ.</span>}</td>
                  <td><label className="sr-only" htmlFor={`v-d-${i}`}>Description de {v.name}</label><input id={`v-d-${i}`} value={v.description ?? ""} onChange={(e) => patch(i, { description: e.target.value })} /></td>
                  <td><button type="button" onClick={() => write(vars.filter((_, j) => j !== i))} aria-label={`Supprimer la variable ${v.name}`}>Supprimer</button></td>
                </tr>
              ))}
              {vars.length === 0 && <tr><td colSpan={5} className="hint">Aucune variable.</td></tr>}
            </tbody>
          </table>
        </div>
        <button type="button" onClick={() => write([...vars, { name: `variable_${vars.length + 1}`, type: "string", value: "" }])}>Ajouter une variable</button>
      </fieldset>
    </section>
  );
}
