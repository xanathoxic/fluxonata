import { useId } from "react";
import type { JsonSchema } from "../api";
import { typeCompatible, type OutputField } from "./graphOps";

const isTemplate = (v: unknown): v is string => typeof v === "string" && /^\s*\{\{.*\}\}\s*$/.test(v);

interface Props {
  name: string;
  schema: JsonSchema;
  required: boolean;
  value: unknown;
  variables: OutputField[];
  onChange: (value: unknown) => void;
}

/** One action parameter: a typed literal control, or a variable from upstream steps (type-checked). */
export function ParamEditor({ name, schema, required, value, variables, onChange }: Props) {
  const id = useId();
  const template = isTemplate(value);
  const label = schema.title ?? name;
  const hint = schema.description;
  let control;
  if (template || schema.type === "string" || (!schema.type && !schema.enum)) {
    control = <input id={id} type="text" value={typeof value === "string" ? value : ""} aria-describedby={hint ? `${id}-d` : undefined}
      onChange={(e) => onChange(e.target.value)} />;
  } else if (schema.enum) {
    control = (
      <select id={id} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>
        {schema.enum.map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
      </select>
    );
  } else if (schema.type === "boolean") {
    control = (
      <select id={id} value={value === true ? "true" : value === false ? "false" : ""} onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value === "true")}>
        <option value="">—</option><option value="true">Oui</option><option value="false">Non</option>
      </select>
    );
  } else if (schema.type === "integer" || schema.type === "number") {
    control = <input id={id} type="number" value={typeof value === "number" ? value : ""} min={schema.minimum} max={schema.maximum}
      onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} />;
  } else {
    control = <input id={id} type="text" value={typeof value === "string" ? value : JSON.stringify(value ?? "")} onChange={(e) => onChange(e.target.value)} />;
  }
  return (
    <div className="field">
      <label htmlFor={id}>{label}{required && <span className="req"> (obligatoire)</span>}</label>
      {control}
      <div className="var-row">
        <label htmlFor={`${id}-v`} className="sr-only">Insérer une variable pour {label}</label>
        <select id={`${id}-v`} value="" onChange={(e) => e.target.value && onChange(`{{ ${e.target.value} }}`)}>
          <option value="">Utiliser une variable…</option>
          {variables.map((v) => {
            const ok = typeCompatible(v.type, schema.type);
            return <option key={v.path} value={v.path} disabled={!ok}>{v.label}{ok ? "" : " (type incompatible)"}</option>;
          })}
        </select>
        {template && <button type="button" onClick={() => onChange(schema.default)}>Valeur fixe</button>}
      </div>
      {hint && <p id={`${id}-d`} className="hint">{hint}</p>}
    </div>
  );
}
