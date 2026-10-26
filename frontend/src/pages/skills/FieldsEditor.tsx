import { FIELD_TYPES, fieldName, type Field, type FieldType } from "./fields";

/** Rows of typed fields (inputs of a skill, or the fields of the expected answer). */
export function FieldsEditor({ legend, fields, onChange, withRequired, minRows, idPrefix, addLabel }: {
  legend: string; fields: Field[]; onChange: (f: Field[]) => void; withRequired: boolean; minRows: number; idPrefix: string; addLabel: string;
}) {
  const set = (i: number, patch: Partial<Field>) => onChange(fields.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  return (
    <fieldset>
      <legend>{legend}</legend>
      {fields.length === 0 && <p className="hint">Aucun champ.</p>}
      {fields.map((f, i) => (
        <div className="edge-row field-row" key={i}>
          <label className="sr-only" htmlFor={`${idPrefix}-n-${i}`}>Nom du champ {i + 1}</label>
          <input id={`${idPrefix}-n-${i}`} value={f.name} className="mono" placeholder="nom_du_champ" onChange={(e) => set(i, { name: fieldName(e.target.value) })} />
          <label className="sr-only" htmlFor={`${idPrefix}-t-${i}`}>Type du champ {i + 1}</label>
          <select id={`${idPrefix}-t-${i}`} value={f.type} onChange={(e) => set(i, { type: e.target.value as FieldType })}>{FIELD_TYPES.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          <label className="sr-only" htmlFor={`${idPrefix}-d-${i}`}>Description du champ {i + 1}</label>
          <input id={`${idPrefix}-d-${i}`} value={f.description} placeholder="Description (facultatif)" onChange={(e) => set(i, { description: e.target.value })} />
          {withRequired && <span className="field-inline"><input type="checkbox" id={`${idPrefix}-r-${i}`} checked={f.required} onChange={(e) => set(i, { required: e.target.checked })} /><label htmlFor={`${idPrefix}-r-${i}`}>Obligatoire</label></span>}
          <button type="button" onClick={() => onChange(fields.filter((_, j) => j !== i))} disabled={fields.length <= minRows} aria-label={`Retirer le champ ${f.name || i + 1}`}>Retirer</button>
        </div>
      ))}
      <button type="button" onClick={() => onChange([...fields, { name: `champ_${fields.length + 1}`, type: "string", required: withRequired, description: "" }])}>{addLabel}</button>
    </fieldset>
  );
}
