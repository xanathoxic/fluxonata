import { useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { ControlInput } from "./ControlInput";
import { IconButton, IconLabel } from "./FormIcon";
import { orderedFields, readingOrder, visibleRows } from "./layout";
import { visibleState } from "./logic";
import { waitingFor } from "./serviceParams";
import { canLoad, fillValue, type State } from "./rules";
import { styleProps } from "./style";
import { isBlock, type FieldOption, type FormDefinition, type FormField } from "./types";
import "./forms.css";

/** Asks the server (which asks the service, through a connection) for the options of a dynamic list, or for the whole
 *  value a custom control is pre-filled with. */
export type LookupFn = (fieldKey: string, params: Record<string, string>, kind?: "options" | "prefill") => Promise<unknown>;
type Values = Record<string, unknown>;

interface Props {
  definition: FormDefinition;
  values: Values;
  onChange: (v: Values) => void;
  errors?: Record<string, string>;
  disabled?: boolean;
  /** Loads the options of a dynamic list; absent in the builder's preview, which then explains it. */
  lookup?: LookupFn;
  /** Rendered after the sections, inside the form's own style (the submit buttons, so they follow its look). */
  children?: ReactNode;
  /** prefix of the ids of the inputs (a form nested in another one, or repeated, needs its own) */
  idPrefix?: string;
  /** one line of a table: the fields side by side in order, a cell kept (empty) for a hidden one, their labels left to the header */
  inline?: boolean;
}

/** The form a person fills in: sections laid out on a 12-column grid, the fields hidden by their rules left out, dressed in
 * the form's style. Used by the public page, the launch pages and the builder's preview, so what is previewed is what is shown. */
export function DefinitionForm({ definition, values, onChange, errors = {}, disabled, lookup, children, idPrefix = "", inline }: Props) {
  const { shown, states, kept } = visibleState(definition, values);
  const set = (key: string, v: unknown) => onChange({ ...values, [key]: v });
  const look = styleProps(definition.style);
  // a value that the rules no longer allow (the choices were limited) is cleared rather than silently kept
  useEffect(() => {
    let next: Values | null = null;
    for (const f of orderedFields(definition)) {
      const fill = states.get(f.key)?.fill;
      if (fill != null) {  // a rule fills the field: show what the server will keep. Also when the same rule hides it: that is how a rule empties what was typed before the field went away (the server drops a hidden field's value anyway)
        const want = fillValue(f.type, fill);
        if (values[f.key] !== want) next = { ...(next ?? values), [f.key]: want };
        continue;
      }
      if (f.compute && shown.has(f.key)) {  // calculated: show what the server will keep
        const want = kept[f.key];
        if (values[f.key] !== want && !(want === undefined && values[f.key] === undefined)) { const n = { ...(next ?? values) }; if (want === undefined) delete n[f.key]; else n[f.key] = want; next = n; }
        continue;
      }
      const allowed = states.get(f.key)?.options;
      const v = values[f.key];
      if (!allowed || v === undefined || v === "" || (Array.isArray(v) && v.length === 0)) continue;
      const picked = Array.isArray(v) ? v : [v];
      if (picked.some((x) => !allowed.includes(String(x)))) next = { ...(next ?? values), [f.key]: Array.isArray(v) ? picked.filter((x) => allowed.includes(String(x))) : "" };
    }
    if (next) onChange(next);
  });
  const scope = `fb-x-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  return (
    <div className={`${look.className}${definition.css ? ` ${scope}` : ""}`} style={look.style}>
      {/* The author's CSS is nested under this form's own class: the server only ever stores a validated, canonical text, and
          the browser reads it as nested rules, so nothing can reach outside the form. */}
      {definition.css && <style>{`.${scope} {\n${definition.css}\n}`}</style>}
      {definition.sections.map((s, i) => {
        const everyField = readingOrder(s.fields);
        const fields = everyField.filter((f) => shown.has(f.key));
        if (inline) {
          const cols = readingOrder(s.fields).filter((f) => !isBlock(f.type));
          return (
            <div key={s.id ?? i} className="fb-grid fb-inline" style={{ gridTemplateColumns: `repeat(${cols.length}, minmax(9rem, 1fr))` }}>
              {cols.map((f) => (
                <div key={f.key} className="fb-cell" data-key={f.key} data-type={f.type}>
                  {shown.has(f.key) && <FieldInput field={f} state={states.get(f.key)} value={values[f.key]} values={values} error={errors[f.key]} disabled={disabled} lookup={lookup} idPrefix={idPrefix} onChange={(v) => set(f.key, v)} />}
                </div>
              ))}
            </div>
          );
        }
        if (fields.length === 0 && s.fields.length > 0) return null;
        const rows = visibleRows(fields, everyField); // the fields under a hidden one move up into its place
        return (
          <fieldset key={s.id ?? i} className="fb-section">
            {s.title && <legend>{s.title}</legend>}
            {s.description && <p className="hint">{s.description}</p>}
            <div className="fb-grid">
              {fields.map((f) => {
                const l = rows.get(f.key);
                const h = l?.height ?? 1;
                const cell = { "--c": l?.col ?? 1, "--s": l?.span ?? 12, "--r": l?.row ?? 1, "--h": h } as CSSProperties;
                return (
                  <div key={f.key} className={`fb-cell${h > 1 ? " fb-tall" : ""}`} style={cell} data-key={f.key} data-type={f.type}>
                    {isBlock(f.type) ? <Block field={{ ...f, label: states.get(f.key)?.label ?? f.label }} /> : <FieldInput field={f} state={states.get(f.key)} value={values[f.key]} values={values} error={errors[f.key]} disabled={disabled} lookup={lookup} idPrefix={idPrefix} onChange={(v) => set(f.key, v)} />}
                  </div>
                );
              })}
            </div>
          </fieldset>
        );
      })}
      {children && <div className="fb-actions">{children}</div>}
    </div>
  );
}

/** Plain text only: whatever a designer types is shown as text, never as markup. */
function Block({ field }: { field: FormField }) {
  if (field.type === "heading") {
    const Tag = (`h${field.level ?? 3}`) as "h2" | "h3" | "h4";
    return <Tag className="fb-heading">{field.label}</Tag>;
  }
  if (field.type === "paragraph") return <p className="fb-paragraph">{field.label}</p>;
  return <hr className="fb-divider" />;
}

function useOptions(field: FormField, values: Values, search: string, lookup?: LookupFn) {
  const [state, setState] = useState<{ options: FieldOption[]; loading: boolean; error: string }>({ options: field.options ?? [], loading: false, error: "" });
  const seq = useRef(0);
  const source = field.source;
  const params: Record<string, string> = {};
  for (const p of source?.params ?? []) params[p] = p === "q" ? search : values[p] === undefined || values[p] === null ? "" : String(values[p]);
  const signature = JSON.stringify(params);
  const waiting = waitingFor(source, values);
  const waitKey = waiting.join(",");
  useEffect(() => {
    if (!source || !lookup) return;
    const mine = ++seq.current;
    if (waitKey) { setState({ options: [], loading: false, error: "" }); return; }
    setState((s) => ({ ...s, loading: true, error: "" }));
    const timer = window.setTimeout(() => {
      lookup(field.key, JSON.parse(signature) as Record<string, string>)
        .then((options) => { if (mine === seq.current) setState({ options: options as FieldOption[], loading: false, error: "" }); })
        .catch((e: unknown) => { if (mine === seq.current) setState({ options: [], loading: false, error: e instanceof Error ? e.message : "La liste n'a pas pu être chargée." }); });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [field.key, source, lookup, signature, waitKey]);
  return { ...state, waiting };
}

interface FieldProps { idPrefix?: string; field: FormField; state?: State; value: unknown; values: Values; error?: string; disabled?: boolean; lookup?: LookupFn; onChange: (v: unknown) => void }

function FieldInput({ field: base, state, value, values, error, disabled: lockedByForm, lookup, onChange, idPrefix = "" }: FieldProps) {
  // a rule may put the field in read-only: shown with its value, not editable (a presentation effect: the server does not enforce it)
  const disabled = lockedByForm || Boolean(state?.readonly);
  // the rules may change the texts, make the field obligatory or limit its choices
  const field: FormField = { ...base, label: state?.label ?? base.label, help: state?.help ?? base.help, placeholder: state?.placeholder ?? base.placeholder, required: state?.required ?? base.required };
  const id = `${idPrefix}fb-${field.key}`;
  const [search, setSearch] = useState("");
  const loaded = useOptions(base, values, search, lookup);
  const { loading, error: loadError, waiting } = loaded;
  // A list that depends on another field: what was chosen under the previous value of that field is no longer offered, so it is dropped
  // (otherwise the old members of another group would stay ticked, invisible, and be sent).
  const dependent = Boolean(lookup) && (base.source?.params ?? []).some((p) => p !== "q");
  useEffect(() => {
    if (!dependent || loading || loadError) return;
    const offered = new Set(loaded.options.map((o) => o.value));
    if (Array.isArray(value)) {
      const kept = (value as string[]).filter((v) => offered.has(v));
      if (kept.length !== value.length) onChange(kept);
    } else if (typeof value === "string" && value !== "" && !offered.has(value)) onChange("");
  }, [dependent, loading, loadError, loaded.options]); // eslint-disable-line react-hooks/exhaustive-deps
  const options = state?.options ? loaded.options.filter((o) => (state.options as string[]).includes(o.value)) : loaded.options;
  const describedBy = [field.help ? `${id}-help` : "", error ? `${id}-err` : ""].filter(Boolean).join(" ") || undefined;
  const filled = state?.fill != null || Boolean(base.compute);  // filled by a rule: shown, not editable (the server keeps the rule's value)
  const common = { id, disabled: disabled || filled, readOnly: filled || undefined, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined, "aria-required": field.required || undefined };
  const text = typeof value === "string" ? value : "";
  const label = <>{field.label}{field.required && <span aria-hidden="true" className="fb-req"> *</span>}</>;
  const choice = (o: FieldOption) => o.label || o.value;
  let control: ReactNode;
  switch (field.type) {
    case "textarea": control = <textarea {...common} rows={4} value={text} placeholder={field.placeholder} onChange={(e) => onChange(e.target.value)} />; break;
    case "number": control = <input {...common} type="number" min={field.min} max={field.max} step="any" value={typeof value === "number" ? value : ""} placeholder={field.placeholder} onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} />; break;
    case "email": control = <input {...common} type="email" autoComplete="email" value={text} placeholder={field.placeholder} onChange={(e) => onChange(e.target.value)} />; break;
    case "date": control = <input {...common} type="date" value={text} onChange={(e) => onChange(e.target.value)} />; break;
    case "select": control = (
      <select {...common} value={text} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choisir…</option>
        {options.map((o) => <option key={o.value} value={o.value}>{choice(o)}</option>)}
      </select>); break;
    case "combobox": control = (
      <>
        <input {...common} role="combobox" aria-expanded="false" list={`${id}-list`} autoComplete="off" value={search !== "" || !text ? search : (options.find((o) => o.value === text)?.label ?? text)} placeholder={field.placeholder ?? "Tapez pour chercher…"}
          onChange={(e) => { setSearch(e.target.value); const m = options.find((o) => choice(o) === e.target.value || o.value === e.target.value); onChange(m ? m.value : ""); }} />
        <datalist id={`${id}-list`}>{options.map((o) => <option key={o.value} value={choice(o)} />)}</datalist>
      </>); break;
    case "radio": control = (
      <div role="radiogroup" aria-labelledby={`${id}-l`} aria-describedby={describedBy} className="fb-choices">
        {options.map((o, i) => <label key={o.value} className="fb-choice"><input type="radio" name={id} id={i === 0 ? id : undefined} disabled={disabled} checked={value === o.value} onChange={() => onChange(o.value)} /> {choice(o)}</label>)}
      </div>); break;
    case "checkbox": control = <label className="fb-choice"><input {...common} type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked)} /> {field.placeholder || "Oui"}</label>; break;
    case "checkbox_list": {
      const picked = Array.isArray(value) ? (value as string[]) : [];
      control = (
        <div role="group" aria-labelledby={`${id}-l`} aria-describedby={describedBy} className="fb-choices">
          {options.map((o, i) => <label key={o.value} className="fb-choice"><input type="checkbox" id={i === 0 ? id : undefined} disabled={disabled} checked={picked.includes(o.value)} onChange={(e) => onChange(e.target.checked ? [...picked, o.value] : picked.filter((p) => p !== o.value))} /> {choice(o)}</label>)}
        </div>); break; }
    case "custom": control = <ControlInput field={field} id={id} value={value} values={values} lookup={lookup} allow={{ options: canLoad(base, state, "options"), prefill: canLoad(base, state, "prefill") }} error={error} disabled={disabled} onChange={onChange} />; break;
    case "datagrid": control = <Grid field={field} id={id} value={value} disabled={disabled} onChange={onChange} />; break;
    default: control = <input {...common} type="text" value={text} placeholder={field.placeholder} onChange={(e) => onChange(e.target.value)} />;
  }
  const labelled = field.type === "radio" || field.type === "checkbox_list" || field.type === "datagrid" || field.type === "custom";
  return (
    <div className={`field fb-field${error ? " has-error" : ""}`}>
      {labelled ? <span id={`${id}-l`} className="fb-label">{label}</span> : <label htmlFor={id}>{label}</label>}
      {control}
      {field.source && !lookup && <p className="hint">Liste dynamique : les choix sont chargés depuis {field.source.connection_from ? `la connexion choisie par « ${field.source.connection_from} »` : `« ${field.source.connection} »`} dans le formulaire publié.</p>}
      {waiting.length > 0 && !loading && <p className="hint" role="status">Les choix apparaîtront quand « {waiting.join(", ")} » sera renseigné.</p>}
      {loading && <p className="hint" role="status">Chargement de la liste…</p>}
      {loadError && <p className="hint" role="status">{loadError}</p>}
      {field.help && <p id={`${id}-help`} className="hint">{field.help}</p>}
      {error && <p id={`${id}-err`} role="alert" className="field-error">{error}</p>}
    </div>
  );
}

type Row = Record<string, string | number | undefined>;

function Grid({ field, id, value, disabled, onChange }: { field: FormField; id: string; value: unknown; disabled?: boolean; onChange: (v: unknown) => void }) {
  const cols = field.columns ?? [];
  const rows: Row[] = Array.isArray(value) ? (value as Row[]) : [];
  const setCell = (r: number, k: string, v: string | number | undefined) => onChange(rows.map((row, i) => (i === r ? { ...row, [k]: v } : row)));
  return (
    <div className="fb-grid-table table-wrap">
      <table className="adm-table">
        <caption className="sr-only">{field.label}</caption>
        <thead><tr>{cols.map((c) => <th key={c.key} scope="col">{c.label || c.key}</th>)}<th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {cols.map((c, ci) => (
                <td key={c.key}><input id={r === 0 && ci === 0 ? id : undefined} disabled={disabled} aria-label={`${c.label || c.key}, ligne ${r + 1}`} type={c.type === "number" ? "number" : "text"} step="any" value={row[c.key] ?? ""}
                  onChange={(e) => setCell(r, c.key, c.type === "number" ? (e.target.value === "" ? undefined : Number(e.target.value)) : e.target.value)} /></td>
              ))}
              <td><IconButton icon="trash" danger disabled={disabled} label={`Supprimer la ligne ${r + 1}`} onClick={() => onChange(rows.filter((_, i) => i !== r))} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" disabled={disabled || rows.length >= 100} onClick={() => onChange([...rows, {}])}><IconLabel icon="plus">Ajouter une ligne</IconLabel></button>
    </div>
  );
}
