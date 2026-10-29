import type { GraphNode } from "./types";

type Form = NonNullable<GraphNode["form"]>;
const TYPES: [string, string][] = [["string", "Texte"], ["integer", "Nombre entier"], ["number", "Nombre"], ["boolean", "Oui / non"]];
const id = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");

/** Fields of the form a person fills in to resume a waiting step (answer = steps.<id>.output.payload.<champ>). */
export function WaitFormFields({ node, onUpdate }: { node: GraphNode; onUpdate: (patch: Partial<GraphNode>) => void }) {
  const form: Form = node.form ?? { properties: {}, required: [] };
  const entries = Object.entries(form.properties);
  const write = (props: Form["properties"], required: string[]) => onUpdate({ form: { properties: props, required: required.filter((r) => r in props) } });
  const rename = (old: string, label: string) => {
    const key = id(label) || old;
    const props = Object.fromEntries(entries.map(([k, v]) => (k === old ? [key, { ...v, title: label }] : [k, v])));
    write(props, form.required.map((r) => (r === old ? key : r)));
  };
  return (
    <fieldset className="bare"><legend>Champs du formulaire de reprise</legend>
      {entries.map(([k, v], i) => (
        <div key={k} className="field field-inline">
          <input aria-label={`Libellé du champ ${i + 1}`} value={v.title ?? k} onChange={(e) => rename(k, e.target.value)} />
          <select aria-label={`Type du champ ${i + 1}`} value={v.type} onChange={(e) => write({ ...form.properties, [k]: { ...v, type: e.target.value } }, form.required)}>{TYPES.map(([t, l]) => <option key={t} value={t}>{l}</option>)}</select>
          <label><input type="checkbox" checked={form.required.includes(k)} onChange={(e) => write(form.properties, e.target.checked ? [...form.required, k] : form.required.filter((r) => r !== k))} /> obligatoire</label>
          <button type="button" aria-label={`Retirer le champ ${i + 1}`} onClick={() => write(Object.fromEntries(entries.filter(([x]) => x !== k)), form.required)}>✕</button>
          <code className="hint">payload.{k}</code>
        </div>))}
      <button type="button" onClick={() => write({ ...form.properties, [`champ_${entries.length + 1}`]: { type: "string", title: `Champ ${entries.length + 1}` } }, form.required)}>Ajouter un champ</button>
    </fieldset>
  );
}
