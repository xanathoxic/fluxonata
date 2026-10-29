import { useEffect, useId, useState } from "react";
import type { JsonSchema } from "../api";
import { CodeEditor } from "./CodeEditor";
import { t } from "../i18n";

type Values = Record<string, unknown>;

export function defaultsFor(schema: JsonSchema): Values {
  const out: Values = {};
  for (const [name, prop] of Object.entries(schema.properties ?? {})) {
    if (prop.default !== undefined) out[name] = prop.default;
  }
  return out;
}

const isStructured = (prop: JsonSchema) => prop.type === "object" || prop.type === "array";
const pretty = (v: unknown) => (v === undefined || v === null ? "" : typeof v === "string" ? v : JSON.stringify(v, null, 2));
const jsonProblem = (text: string) => { try { JSON.parse(text); return ""; } catch (e) { return (e as Error).message.replace(/^JSON\.parse: /, "").replace(/ in JSON at position (\d+).*$/, " (position $1)"); } };

/**
 * Objects and arrays are edited as JSON. The parsed value goes into the form values as soon as the text is valid; while it is not,
 * the raw text is kept (and reported by validateValues) so that nothing the user typed is lost or silently replaced.
 */
function JsonField({ prop, value, disabled, describedBy, invalid, onChange }: {
  prop: JsonSchema; value: unknown; disabled?: boolean; describedBy?: string; invalid: boolean; onChange: (v: unknown) => void;
}) {
  const [text, setText] = useState(() => pretty(value));
  useEffect(() => { // the form was filled from outside (example, saved test set): show the new value
    if (typeof value === "string") return;
    let current: unknown;
    try { current = text.trim() === "" ? undefined : JSON.parse(text); } catch { current = Symbol("invalid"); }
    if (JSON.stringify(current) !== JSON.stringify(value)) setText(pretty(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only external value changes matter
  }, [value]);
  const edit = (next: string) => {
    setText(next);
    if (next.trim() === "") return onChange(undefined);
    try { onChange(JSON.parse(next)); } catch { onChange(next); }
  };
  const problem = text.trim() === "" ? "" : jsonProblem(text);
  const format = () => { try { const v: unknown = JSON.parse(text); setText(pretty(v)); onChange(v); } catch { /* the error is already shown */ } };
  return (
    <div className={`json-field${invalid ? " json-invalid" : ""}`} aria-describedby={describedBy}>
      <CodeEditor value={text} onChange={edit} language="json" readOnly={disabled} minHeight="96px" ariaLabel={`${prop.title ?? "Valeur"} (JSON ${prop.type === "array" ? "liste" : "objet"})`} />
      <div className="json-bar">
        <span className={problem ? "json-state err" : "json-state"} role="status">{text.trim() === "" ? "Vide" : problem ? `JSON invalide : ${problem}` : "JSON valide ✓"}</span>
        <button type="button" onClick={format} disabled={disabled || !!problem || text.trim() === ""}>Mettre en forme</button>
      </div>
    </div>
  );
}

/** Client-side check mirroring the server (which stays authoritative). Returns {field: message}. */
export function validateValues(schema: JsonSchema, values: Values): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const [name, prop] of Object.entries(schema.properties ?? {})) {
    const v = values[name];
    const empty = v === undefined || v === "" || v === null;
    if (empty) {
      if (schema.required?.includes(name)) errors[name] = "Ce champ est obligatoire.";
      continue;
    }
    if (isStructured(prop) && typeof v === "string") { errors[name] = `JSON invalide : ${jsonProblem(v)}`; continue; } // text the user typed that does not parse yet
    if (typeof v === "string") {
      if (prop.minLength !== undefined && v.length < prop.minLength) errors[name] = `Au moins ${prop.minLength} caractère(s).`;
      if (prop.maxLength !== undefined && v.length > prop.maxLength) errors[name] = `Au plus ${prop.maxLength} caractères.`;
    }
    if (typeof v === "number") {
      if (prop.type === "integer" && !Number.isInteger(v)) errors[name] = "Entrez un nombre entier.";
      if (prop.minimum !== undefined && v < prop.minimum) errors[name] = `Minimum : ${prop.minimum}.`;
      if (prop.maximum !== undefined && v > prop.maximum) errors[name] = `Maximum : ${prop.maximum}.`;
    }
  }
  return errors;
}

interface Props {
  schema: JsonSchema;
  values: Values;
  onChange: (values: Values) => void;
  errors?: Record<string, string>;
  disabled?: boolean;
}

export function SchemaForm({ schema, values, onChange, errors = {}, disabled }: Props) {
  const base = useId();
  const set = (name: string, v: unknown) => onChange({ ...values, [name]: v });

  return (
    <>
      {Object.entries(schema.properties ?? {}).map(([name, prop]) => {
        const id = `${base}-${name}`;
        const required = schema.required?.includes(name) ?? false;
        const err = errors[name];
        const describedBy = [prop.description ? `${id}-d` : "", err ? `${id}-e` : ""].filter(Boolean).join(" ") || undefined;
        const label = (
          <label htmlFor={id}>
            {prop.title ?? name}
            {required && <span className="req"> ({t("common.required")})</span>}
          </label>
        );
        let control;
        const value = values[name];
        if (prop.type === "boolean") {
          control = (
            <input id={id} type="checkbox" checked={value === true} disabled={disabled} aria-describedby={describedBy}
              onChange={(e) => set(name, e.target.checked)} />
          );
        } else if (prop.enum) {
          control = (
            <select id={id} value={String(value ?? "")} disabled={disabled} aria-describedby={describedBy} aria-invalid={!!err}
              onChange={(e) => set(name, e.target.value)}>
              <option value="">—</option>
              {prop.enum.map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
            </select>
          );
        } else if (prop.type === "integer" || prop.type === "number") {
          control = (
            <input id={id} type="number" inputMode="numeric" value={typeof value === "number" ? value : ""} min={prop.minimum}
              max={prop.maximum} step={prop.type === "integer" ? 1 : "any"} disabled={disabled} aria-describedby={describedBy}
              aria-invalid={!!err} aria-required={required}
              onChange={(e) => set(name, e.target.value === "" ? undefined : Number(e.target.value))} />
          );
        } else if (isStructured(prop)) {
          control = <JsonField prop={prop} value={value} disabled={disabled} describedBy={describedBy} invalid={!!err} onChange={(v) => set(name, v)} />;
        } else {
          const long = (prop.maxLength ?? 0) > 200;
          const common = {
            id, value: String(value ?? ""), disabled, "aria-describedby": describedBy, "aria-invalid": !!err,
            "aria-required": required, maxLength: prop.maxLength,
          };
          control = long
            ? <textarea rows={5} {...common} onChange={(e) => set(name, e.target.value)} />
            : <input type={prop.writeOnly || prop.format === "password" ? "password" : "text"} {...common}
                onChange={(e) => set(name, e.target.value)} />;
        }
        return (
          <div className={`field${prop.type === "boolean" ? " field-inline" : ""}`} key={name}>
            {label}
            {control}
            {prop.description && <p id={`${id}-d`} className="hint">{prop.description}{isStructured(prop) ? ` Saisie au format JSON (${prop.type === "array" ? "liste" : "objet"}).` : ""}</p>}
            {err && <p id={`${id}-e`} role="alert" className="field-error">Erreur : {err}</p>}
          </div>
        );
      })}
    </>
  );
}
