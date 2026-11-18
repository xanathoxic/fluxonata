import { useState } from "react";
import { useDraft } from "../DraftContext";
import { VAR_TYPES as BASE_TYPES } from "../ScriptParts";
import type { VariableType, Variable } from "../types";
import { HelpTip } from "../../components/HelpTip";

const VAR_TYPES: [VariableType, string][] = [...BASE_TYPES, ["json", "JSON"]];

/** Valeur JSON : zone de texte à chasse fixe, mise en forme à la demande, erreur de syntaxe affichée sans perdre la dernière valeur valide. */
function JsonInput({ value, onChange, id, emptyHint }: { value: unknown; onChange: (val: unknown) => void; id: string; emptyHint: string }) {
  const show = (val: unknown) => (val === undefined || val === null ? "" : JSON.stringify(val, null, 2));
  const [text, setText] = useState(() => show(value));
  let error = "";
  if (text.trim() !== "") {
    try { JSON.parse(text); } catch (e) { error = e instanceof Error ? e.message : "JSON invalide"; }
  }
  const edit = (t: string) => {
    setText(t);
    if (t.trim() === "") return onChange(undefined);
    try { onChange(JSON.parse(t)); } catch { /* la dernière valeur valide reste enregistrée */ }
  };
  const format = () => { try { setText(JSON.stringify(JSON.parse(text), null, 2)); } catch { /* erreur déjà affichée */ } };
  return (
    <>
      <textarea id={id} className="mono var-json" rows={Math.min(14, Math.max(4, text.split("\n").length))} spellCheck={false} value={text} aria-invalid={error !== ""} aria-describedby={`${id}-h`} onChange={(e) => edit(e.target.value)} />
      <div className="var-json-bar">
        <button type="button" onClick={format} disabled={error !== "" || text.trim() === ""}>Formater</button>
        {error ? <span id={`${id}-h`} role="alert" className="field-error">JSON invalide : {error}</span> : <span id={`${id}-h`} className="hint">{text.trim() === "" ? emptyHint : "JSON valide."}</span>}
      </div>
    </>
  );
}

function ValueInput({ v, onChange, id }: { v: Variable; onChange: (val: unknown) => void; id: string }) {
  if (v.type === "json") return <JsonInput key={v.name} value={v.value} onChange={(val) => onChange(val ?? null)} id={id} emptyHint="Vide : la variable vaut null." />;
  if (v.type === "boolean") return <select id={id} value={String(v.value === true)} onChange={(e) => onChange(e.target.value === "true")}><option value="false">Non</option><option value="true">Oui</option></select>;
  if (v.type === "integer" || v.type === "number") return <input id={id} type="number" value={typeof v.value === "number" ? v.value : ""} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />;
  if (v.type === "array" || v.type === "object") return <input id={id} className="mono" defaultValue={JSON.stringify(v.value ?? (v.type === "array" ? [] : {}))} onBlur={(e) => { try { onChange(JSON.parse(e.target.value)); } catch { /* keep previous value until valid JSON */ } }} aria-describedby={`${id}-h`} />;
  return <input id={id} value={typeof v.value === "string" ? v.value : ""} onChange={(e) => onChange(e.target.value)} />;
}

const ENVS: ["dev" | "test" | "prod", string][] = [["dev", "Développement"], ["test", "Recette"], ["prod", "Production"]];

/** Optional value per environment (empty = the default value above). Typed values are kept typed. */
function EnvValues({ v, onChange }: { v: Variable; onChange: (env: Record<string, unknown> | undefined) => void }) {
  const set = (k: string, raw: string) => {
    const next: Record<string, unknown> = { ...(v.env_values ?? {}) };
    if (raw === "") delete next[k];
    else if (v.type === "integer" || v.type === "number") next[k] = Number(raw);
    else if (v.type === "boolean") next[k] = raw === "true";
    else if (v.type === "array" || v.type === "object" || v.type === "json") { try { next[k] = JSON.parse(raw); } catch { return; } }
    else next[k] = raw;
    onChange(Object.keys(next).length ? next : undefined);
  };
  const setJson = (k: string, val: unknown) => {
    const next: Record<string, unknown> = { ...(v.env_values ?? {}) };
    if (val === undefined) delete next[k];
    else next[k] = val;
    onChange(Object.keys(next).length ? next : undefined);
  };
  return (
    <details><summary>Par environnement</summary>
      {ENVS.map(([k, label]) => (
        <div className="field" key={k}><label htmlFor={`ev-${v.name}-${k}`}>{label} (vide = valeur par défaut)</label>
          {v.type === "json" ? <JsonInput value={v.env_values?.[k]} id={`ev-${v.name}-${k}`} emptyHint="Vide : valeur par défaut." onChange={(val) => setJson(k, val)} /> : <input id={`ev-${v.name}-${k}`} defaultValue={v.env_values?.[k] === undefined ? "" : typeof v.env_values[k] === "string" ? String(v.env_values[k]) : JSON.stringify(v.env_values[k])} onBlur={(e) => set(k, e.target.value)} />}</div>))}
    </details>
  );
}

/** Workflow variables : named constants readable everywhere as vars.<name> and in scripts. */
export function VariablesTab() {
  const { graph, setGraph, editable } = useDraft();
  const vars = graph.variables ?? [];
  const write = (next: Variable[]) => setGraph({ ...graph, variables: next });
  const patch = (i: number, p: Partial<Variable>) => write(vars.map((v, j) => (j === i ? { ...v, ...p } : v)));
  return (
    <section aria-label="Variables du workflow">
      <h2>Variables <HelpTip>Constantes du workflow : lisibles dans les expressions (<code>{"{{ vars.nom }}"}</code>) et dans les scripts (<code>vars["nom"]</code>). Elles ne contiennent jamais de secret : utilisez une connexion.</HelpTip></h2>
      <fieldset disabled={!editable} className="bare">
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Variables du workflow</caption>
            <thead><tr><th scope="col">Nom</th><th scope="col">Type</th><th scope="col">Valeur</th><th scope="col">Description</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {vars.map((v, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: liste affichée en entier, sans réordonnancement ni état par ligne
                <tr key={i}>
                  <td><label className="sr-only" htmlFor={`v-n-${i}`}>Nom de la variable {i + 1}</label><input id={`v-n-${i}`} value={v.name} pattern="[A-Za-z_][A-Za-z0-9_]*" onChange={(e) => patch(i, { name: e.target.value.replace(/[^A-Za-z0-9_]/g, "_") })} /></td>
                  <td><label className="sr-only" htmlFor={`v-t-${i}`}>Type de {v.name}</label>
                    <select id={`v-t-${i}`} value={v.type} onChange={(e) => patch(i, { type: e.target.value as VariableType, value: e.target.value === "boolean" ? false : e.target.value === "array" ? [] : e.target.value === "object" ? {} : null })}>
                      {VAR_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
                  <td><label className="sr-only" htmlFor={`v-v-${i}`}>Valeur de {v.name}</label><ValueInput id={`v-v-${i}`} v={v} onChange={(val) => patch(i, { value: val })} />
                    <EnvValues v={v} onChange={(env) => patch(i, { env_values: env as Variable["env_values"] })} />
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
