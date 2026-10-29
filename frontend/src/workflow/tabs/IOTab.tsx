import { allVariables } from "../graphOps";
import { Section } from "../../components/AdminUi";
import { useDraft } from "../DraftContext";
import { VAR_TYPES } from "../ScriptParts";
import type { JsonSchema } from "../../api";

type Row = { name: string; schema: JsonSchema; required: boolean };

/** Workflow inputs (they generate the launch form and the API contract) and outputs (expressions on step results). */
export function IOTab() {
  const { graph, setGraph, editable, actions } = useDraft();
  const props = graph.inputs.properties ?? {};
  const rows: Row[] = Object.entries(props).map(([name, schema]) => ({ name, schema, required: graph.inputs.required?.includes(name) ?? false }));
  const write = (next: Row[]) => setGraph({ ...graph, inputs: { type: "object", properties: Object.fromEntries(next.map((r) => [r.name, r.schema])), required: next.filter((r) => r.required).map((r) => r.name) } });
  const patchRow = (i: number, p: Partial<Row> & { schema?: JsonSchema }) => write(rows.map((r, j) => (j === i ? { ...r, ...p, schema: { ...r.schema, ...(p.schema ?? {}) } } : r)));
  const outputs = Object.entries(graph.outputs);
  const picker = allVariables(graph, actions);
  return (
    <section aria-label="Entrées et sorties du workflow">
      <fieldset disabled={!editable} className="bare">
      <Section title="Entrées" hint="Ces champs forment le formulaire de lancement et le contrat de l'API générée.">
        <div className="table-wrap">
          <table className="adm-table">
            <caption className="sr-only">Entrées du workflow</caption>
            <thead><tr><th scope="col">Identifiant</th><th scope="col">Libellé</th><th scope="col">Type</th><th scope="col">Obligatoire</th><th scope="col">Valeur par défaut</th><th scope="col">Description</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td><label className="sr-only" htmlFor={`in-n-${i}`}>Identifiant technique de l'entrée {i + 1}</label><input id={`in-n-${i}`} value={r.name} pattern="[a-z][a-z0-9_]*" onChange={(e) => write(rows.map((x, k) => (k === i ? { ...x, name: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") } : x)))} /></td>
                  <td><label className="sr-only" htmlFor={`in-t-${i}`}>Libellé de {r.name}</label><input id={`in-t-${i}`} value={r.schema.title ?? ""} onChange={(e) => patchRow(i, { schema: { title: e.target.value } })} /></td>
                  <td><label className="sr-only" htmlFor={`in-y-${i}`}>Type de {r.name}</label>
                    <select id={`in-y-${i}`} value={String(r.schema.type ?? "string")} onChange={(e) => patchRow(i, { schema: { type: e.target.value } })}>{VAR_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
                  <td><input type="checkbox" id={`in-r-${i}`} checked={r.required} onChange={(e) => patchRow(i, { required: e.target.checked })} /><label htmlFor={`in-r-${i}`} className="sr-only">{r.name} obligatoire</label></td>
                  <td><label className="sr-only" htmlFor={`in-d-${i}`}>Valeur par défaut de {r.name}</label>
                    <input id={`in-d-${i}`} value={r.schema.default === undefined ? "" : String(r.schema.default)} onChange={(e) => { const raw = e.target.value; const t = r.schema.type; const val = raw === "" ? undefined : t === "integer" || t === "number" ? Number(raw) : t === "boolean" ? raw === "true" : raw; patchRow(i, { schema: { default: val } }); }} /></td>
                  <td><label className="sr-only" htmlFor={`in-e-${i}`}>Description de {r.name}</label><input id={`in-e-${i}`} value={r.schema.description ?? ""} onChange={(e) => patchRow(i, { schema: { description: e.target.value } })} /></td>
                  <td><button type="button" onClick={() => write(rows.filter((_, k) => k !== i))} aria-label={`Supprimer l'entrée ${r.name}`}>Supprimer</button></td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={7} className="hint">Aucune entrée.</td></tr>}
            </tbody>
          </table>
        </div>
        <button type="button" onClick={() => write([...rows, { name: `champ_${rows.length + 1}`, schema: { type: "string", title: `Champ ${rows.length + 1}` }, required: false }])}>Ajouter une entrée</button>

      </Section>
      <Section title="Sorties" hint="Valeurs renvoyées à la fin du workflow (expressions sur les résultats des étapes).">
        {outputs.map(([k, v], i) => (
          <div className="edge-row" key={i}>
            <label className="sr-only" htmlFor={`out-k-${i}`}>Nom de la sortie {i + 1}</label>
            <input id={`out-k-${i}`} value={k} onChange={(e) => setGraph({ ...graph, outputs: Object.fromEntries(outputs.map(([kk, vv], j) => (j === i ? [e.target.value, vv] : [kk, vv]))) })} />
            <label className="sr-only" htmlFor={`out-v-${i}`}>Valeur de la sortie {k}</label>
            <input id={`out-v-${i}`} value={v} placeholder="{{ steps.etape.output.champ }}" onChange={(e) => setGraph({ ...graph, outputs: Object.fromEntries(outputs.map(([kk, vv], j) => (j === i ? [kk, e.target.value] : [kk, vv]))) })} />
            <label className="sr-only" htmlFor={`out-p-${i}`}>Choisir une variable pour la sortie {k}</label>
            <select id={`out-p-${i}`} value="" onChange={(e) => e.target.value && setGraph({ ...graph, outputs: Object.fromEntries(outputs.map(([kk, vv], j) => (j === i ? [kk, `{{ ${e.target.value} }}`] : [kk, vv]))) })}>
              <option value="">Variable…</option>{picker.map((p) => <option key={p.path} value={p.path}>{p.label}</option>)}</select>
            <button type="button" onClick={() => setGraph({ ...graph, outputs: Object.fromEntries(outputs.filter((_, j) => j !== i)) })}>Retirer</button>
          </div>
        ))}
        <button type="button" onClick={() => setGraph({ ...graph, outputs: { ...graph.outputs, [`sortie_${outputs.length + 1}`]: "" } })}>Ajouter une sortie</button>
      </Section>
      </fieldset>
    </section>
  );
}
