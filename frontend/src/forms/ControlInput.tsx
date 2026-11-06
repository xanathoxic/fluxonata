import { DefinitionForm } from "./DefinitionForm";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { LookupFn } from "./DefinitionForm";
import { OptionPicker } from "./OptionPicker";
import { detailsOf, hasDetails, itemsOf, MAX_ROWS, regroup, rowDefinition, seedOf, type ControlItem, type ControlPrefill } from "./controls";
import { FormIcon, IconButton, IconLabel } from "./FormIcon";
import { isEmpty } from "./logic";
import { validateDefinition } from "./logic";
import { waitingFor } from "./serviceParams";
import { isBlock, type DataSource, type FormField } from "./types";

type Row = Record<string, unknown>;
const opt = (o: ControlItem) => <option key={o.value} value={o.value} disabled={o.disabled}>{o.label || o.value}</option>;
/** consecutive options sharing a group, in order ("" = no group) */
const groupsOf = (items: ControlItem[]): [string, ControlItem[]][] => items.reduce<[string, ControlItem[]][]>((out, o) => { const g = o.group ?? ""; const last = out[out.length - 1]; if (last && last[0] === g) last[1].push(o); else out.push([g, [o]]); return out; }, []);
const asRow = (v: unknown): Row => (v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Row) : {});

interface Props { field: FormField; id: string; value: unknown; /** the values of the whole form: they fill the placeholders of the service's path */ values?: Row; lookup?: LookupFn; /** which service readings may load now (the field's rules may put them under conditions) */ allow?: { options: boolean; prefill: boolean }; error?: string; disabled?: boolean; onChange: (v: unknown) => void }

const paramsOf = (names: string[] | undefined, values: Row): Record<string, string> => Object.fromEntries((names ?? []).map((p) => [p, values[p] === undefined || values[p] === null ? "" : String(values[p])]));

/** The options a service gives (after the fixed ones), asked of the server, which asks the service. */
function useServiceItems(key: string, source: DataSource | undefined, values: Row, lookup: LookupFn | undefined, enabled: boolean) {
  const params = JSON.stringify(paramsOf(source?.params, values));
  const waiting = waitingFor(source, values);
  const waitKey = waiting.join(",");
  const [state, setState] = useState<{ items: ControlItem[]; loading: boolean; error: string }>({ items: [], loading: false, error: "" });
  useEffect(() => {
    if (!source || !lookup || !enabled || waitKey) { setState({ items: [], loading: false, error: "" }); return; }
    let live = true;
    setState((s) => ({ ...s, loading: true, error: "" }));
    const timer = window.setTimeout(() => {
      lookup(key, JSON.parse(params) as Record<string, string>)
        .then((r) => { if (live) setState({ items: r as ControlItem[], loading: false, error: "" }); })
        .catch((e: unknown) => { if (live) setState({ items: [], loading: false, error: e instanceof Error ? e.message : "La liste n'a pas pu être chargée." }); });
    }, 300);
    return () => { live = false; window.clearTimeout(timer); };
  }, [key, Boolean(source), lookup, params, enabled, waitKey]); // eslint-disable-line react-hooks/exhaustive-deps
  return { ...state, waiting: enabled ? waiting : [] };
}

/** The whole value a service pre-fills the control with, while the person has not touched it; they may ask for it again. */
function usePrefill(key: string, cfg: ControlPrefill | undefined, values: Row, lookup: LookupFn | undefined, enabled: boolean, value: unknown, fill: (v: unknown) => void, touched: { current: boolean }) {
  const params = JSON.stringify(paramsOf(cfg?.params, values));
  const [state, setState] = useState<{ loading: boolean; error: string; done: boolean }>({ loading: false, error: "", done: false });
  const ours = useRef<string | null>(null);
  const current = useRef(value);
  current.current = value;
  const run = useCallback(() => {
    if (!cfg || !lookup || !enabled) return () => undefined;
    let live = true;
    setState((s) => ({ ...s, loading: true, error: "" }));
    lookup(key, JSON.parse(params) as Record<string, string>, "prefill")
      .then((r) => {
        if (!live) return;
        const mine = ours.current !== null && JSON.stringify(current.current) === ours.current;
        if (!touched.current && (isEmpty(current.current) || mine)) { ours.current = JSON.stringify(r); touched.current = false; fill(r); }
        setState({ loading: false, error: "", done: true });
      })
      .catch((e: unknown) => { if (live) setState({ loading: false, error: e instanceof Error ? e.message : "Le service n'a pas répondu.", done: false }); });
    return () => { live = false; };
  }, [key, Boolean(cfg), lookup, params, enabled]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!cfg || !lookup || !enabled || touched.current) return;
    const timer = window.setTimeout(() => { void run(); }, 300);
    return () => window.clearTimeout(timer);
  }, [run]); // eslint-disable-line react-hooks/exhaustive-deps
  return state;
}

/** A custom control, as the person fills it in: a group of fields, repeated lines, or boxes to tick with their details. Each line is
 * a small form (rules, calculations and checks of its sub-fields apply); the server checks the whole value again. */
export function ControlInput({ field, id, value, values = {}, lookup, allow = { options: true, prefill: true }, error, disabled, onChange: setValue }: Props) {
  const spec = field.control?.spec;
  const touched = useRef(false);
  const onChange = (v: unknown) => { touched.current = true; setValue(v); };
  const service = useServiceItems(field.key, field.source, values, lookup, allow.options);
  const prefill = usePrefill(field.key, field.prefill, values, lookup, allow.prefill, value, setValue, touched);
  if (!spec) return <p className="hint">Contrôle inconnu.</p>;
  const fixed = itemsOf(field);
  const allItems: ControlItem[] = regroup([...fixed, ...service.items.filter((o) => !fixed.some((f) => f.value === o.value))]);
  const status = (
    <>
      {field.source && lookup && service.waiting.length > 0 && !service.loading && <p className="hint" role="status">Les options apparaîtront quand « {service.waiting.join(", ")} » sera renseigné.</p>}
      {field.source && lookup && service.loading && <p className="hint" role="status">Chargement des options…</p>}
      {field.source && lookup && service.error && <p className="hint" role="status">{service.error}</p>}
      {field.source && !lookup && <p className="hint">Options lues depuis {field.source.connection_from ? `la connexion choisie par « ${field.source.connection_from} »` : `« ${field.source.connection} »`} : elles sont chargées dans le formulaire publié.</p>}
      {field.prefill && !lookup && <p className="hint">Ce contrôle se remplit depuis « {field.prefill.connection} » dans le formulaire publié.</p>}
      {field.prefill && lookup && prefill.error && <p className="hint" role="status">{prefill.error}</p>}
    </>
  );
  const render = (): ReactNode => {
  const formOf = (parts: FormField[], row: Row, set: (r: Row) => void, key: string, inline?: boolean) => {
      const d = rowDefinition(parts);
      return <DefinitionForm definition={d} values={row} onChange={set} errors={error ? validateDefinition(d, row) : {}} disabled={disabled} idPrefix={`${id}-${key}-`} inline={inline} />;
    };
    const form = (row: Row, set: (r: Row) => void, key: string) => formOf(spec.parts ?? [], row, set, key);

    if (spec.mode === "group") return <div className="ctl ctl-group">{form(asRow(value), onChange, "g")}</div>;

    if (spec.mode === "repeat") {
      const rows = Array.isArray(value) ? (value as unknown[]).map(asRow) : [];
      const floor = Math.max(spec.min ?? 0, field.required ? 1 : 0);
      const shown = rows.length < floor ? [...rows, ...Array.from({ length: floor - rows.length }, () => ({}) as Row)] : rows;
      const max = Math.min(spec.max ?? MAX_ROWS, MAX_ROWS);
      const label = spec.row_label || "Ligne";
      const set = (next: Row[]) => onChange(next);
      const table = spec.display === "table";
      const swap = (i: number, j: number) => { const c = [...shown]; [c[i], c[j]] = [c[j] as Row, c[i] as Row]; set(c); };
      if (table) {
        const cols = (spec.parts ?? []).filter((p) => !isBlock(p.type));
        const grid = { gridTemplateColumns: `repeat(${cols.length}, minmax(9rem, 1fr))` };
        const sums = (spec.totals ?? []).map((k) => [k, shown.reduce((t, r) => t + (typeof r[k] === "number" ? (r[k] as number) : 0), 0)] as const);
        return (
          <div className="ctl ctl-repeat" role="group" aria-labelledby={`${id}-l`}>
            <div className="ctl-table">
              <div className="ctl-trow ctl-thead" aria-hidden="true">
                <div className="fb-grid fb-inline" style={grid}>{cols.map((c) => <div key={c.key} className="fb-cell">{c.label || c.key}</div>)}</div>
                <span className="ctl-tools" />
              </div>
              {shown.map((r, i) => (
                <div key={i} className="ctl-trow" role="group" aria-label={`${label} ${i + 1}`}>
                  {formOf(spec.parts ?? [], r, (next) => set(shown.map((x, k) => (k === i ? next : x))), `r${i}`, true)}
                  {!disabled && (
                    <span className="ctl-tools">
                      <IconButton icon="up" label={`Monter ${label.toLowerCase()} ${i + 1}`} disabled={i === 0} onClick={() => swap(i, i - 1)} />
                      <IconButton icon="down" label={`Descendre ${label.toLowerCase()} ${i + 1}`} disabled={i === shown.length - 1} onClick={() => swap(i, i + 1)} />
                      <IconButton icon="trash" danger label={`Supprimer ${label.toLowerCase()} ${i + 1}`} disabled={shown.length <= floor} onClick={() => set(shown.filter((_, k) => k !== i))} />
                    </span>
                  )}
                </div>
              ))}
              {sums.length > 0 && (
                <div className="ctl-trow ctl-tfoot" aria-label="Totaux">
                  <div className="fb-grid fb-inline" style={grid}>{cols.map((c) => <div key={c.key} className="fb-cell">{(spec.totals ?? []).includes(c.key) ? <span>Total {c.label || c.key} : {Math.round((sums.find(([k]) => k === c.key)?.[1] ?? 0) * 1e6) / 1e6}</span> : null}</div>)}</div>
                  <span className="ctl-tools" />
                </div>
              )}
            </div>
            {!disabled && <button type="button" onClick={() => set([...shown, {}])} disabled={shown.length >= max}><IconLabel icon="plus">{spec.add_label || "Ajouter une ligne"}</IconLabel></button>}
            <p className="hint">{shown.length} / {max} ligne(s){floor ? ` · au moins ${floor}` : ""}</p>
          </div>
        );
      }
      return (
        <div className="ctl ctl-repeat" role="group" aria-labelledby={`${id}-l`}>
          {shown.map((r, i) => (
            <div key={i} className="ctl-row" role="group" aria-label={`${label} ${i + 1}`}>
              <div className="ctl-rowhead">
                <strong>{label} {i + 1}</strong>
                {!disabled && (
                  <span className="ctl-rowtools">
                    <IconButton icon="up" label={`Monter ${label.toLowerCase()} ${i + 1}`} disabled={i === 0} onClick={() => swap(i, i - 1)} />
                    <IconButton icon="down" label={`Descendre ${label.toLowerCase()} ${i + 1}`} disabled={i === shown.length - 1} onClick={() => swap(i, i + 1)} />
                    <IconButton icon="trash" danger label={`Supprimer ${label.toLowerCase()} ${i + 1}`} disabled={shown.length <= floor} onClick={() => set(shown.filter((_, k) => k !== i))} />
                  </span>
                )}
              </div>
              {form(r, (next) => set(shown.map((x, k) => (k === i ? next : x))), `r${i}`)}
            </div>
          ))}
          {!disabled && <button type="button" onClick={() => set([...shown, {}])} disabled={shown.length >= max}><IconLabel icon="plus">{spec.add_label || "Ajouter une ligne"}</IconLabel></button>}
          <p className="hint">{shown.length} / {max} ligne(s){floor ? ` · au moins ${floor}` : ""}</p>
        </div>
      );
    }

    if (spec.mode === "choice") {
      const items = allItems;
      const raw = typeof value === "string" ? { value } : asRow(value);
      const current = typeof raw.value === "string" ? items.find((o) => o.value === raw.value) : undefined;
      const details = hasDetails(spec);
      const pick = (v: string) => onChange(v === "" ? undefined : details ? { ...seedOf(spec, items.find((o) => o.value === v)), value: v } : v);
      const parts = detailsOf(spec, current);
      return (
        <div className="ctl ctl-choice" role="group" aria-labelledby={`${id}-l`}>
          {spec.display === "dropdown" ? (
            <OptionPicker id={id} items={items} selected={current ? [current.value] : []} multiple={false} placeholder={spec.placeholder} disabled={disabled} invalid={Boolean(error)} onToggle={(v) => pick(v)} />
          ) : spec.display === "radio" ? (
            <div role="radiogroup" aria-labelledby={`${id}-l`} className="fb-choices">
              {items.map((o, i) => <label key={o.value} className="fb-choice"><input type="radio" name={id} id={i === 0 ? id : undefined} disabled={disabled} checked={current?.value === o.value} onChange={() => pick(o.value)} /> {o.label || o.value}</label>)}
            </div>
          ) : (
            <select id={id} disabled={disabled} value={current?.value ?? ""} onChange={(e) => pick(e.target.value)} aria-invalid={error ? true : undefined}>
              <option value="">Choisir…</option>
              {groupsOf(items).map(([g, os]) => g ? <optgroup key={g} label={g}>{os.map(opt)}</optgroup> : os.map(opt))}
            </select>
          )}
          {current && details && parts.some((p) => !isBlock(p.type)) && (
            <div className="ctl-details" key={current.value}>{formOf(parts, Object.fromEntries(Object.entries(raw).filter(([k]) => k !== "value")), (next) => onChange({ ...next, value: current.value }), `c-${current.value}`)}</div>
          )}
        </div>
      );
    }

    // items: boxes to tick, each opening its details
    const items = allItems;
    const details = hasDetails(spec);
    const picked = new Map<string, Row>();
    for (const raw of Array.isArray(value) ? (value as unknown[]) : []) {
      if (typeof raw === "string") picked.set(raw, {});
      else if (raw && typeof raw === "object" && typeof (raw as Row).value === "string") picked.set((raw as Row).value as string, asRow(raw));
    }
    const emit = (next: Map<string, Row>) => {
      const ordered = items.filter((o) => next.has(o.value));
      onChange(details ? ordered.map((o) => ({ ...(next.get(o.value) as Row), value: o.value })) : ordered.map((o) => o.value));
    };
    const max = spec.max ?? items.length;
    const dropdown = spec.display === "dropdown";
    const seed = (v: string): Row => (details ? seedOf(spec, items.find((o) => o.value === v)) : {});
    const toggle = (v: string, on: boolean) => { const n = new Map(picked); if (on) n.set(v, seed(v)); else n.delete(v); emit(n); };
    return (
      <div className="ctl ctl-items" role="group" aria-labelledby={`${id}-l`}>
        {dropdown && <OptionPicker id={id} items={items} selected={[...picked.keys()]} multiple placeholder={spec.placeholder} disabled={disabled} invalid={Boolean(error)} max={spec.max} onToggle={toggle} />}
        {items.map((o, i) => {
          if (dropdown && !(picked.has(o.value) && details && detailsOf(spec, o).some((p) => !isBlock(p.type)))) return null;
          const on = picked.has(o.value);
          const row = picked.get(o.value) ?? {};
          return (
            <div key={o.value} className={`ctl-item${on ? " on" : ""}`}>
              {!dropdown && o.group && o.group !== items[i - 1]?.group && <p className="opk-group">{o.group}</p>}
              <label className="fb-choice">
                <input type="checkbox" id={!dropdown && i === 0 ? id : undefined} disabled={disabled || o.disabled || (!on && picked.size >= max)} checked={on} onChange={(e) => { const n = new Map(picked); if (e.target.checked) n.set(o.value, seed(o.value)); else n.delete(o.value); emit(n); }} /> {o.label || o.value}
              </label>
              {on && details && detailsOf(spec, o).some((p) => !isBlock(p.type)) && <div className="ctl-details">{formOf(detailsOf(spec, o), row, (next) => { const n = new Map(picked); n.set(o.value, next); emit(n); }, `i${i}`)}</div>}
            </div>
          );
        })}
        <p className="hint"><FormIcon name="boxes" size={14} /> {picked.size} coché(s){spec.min || field.required ? ` · au moins ${Math.max(spec.min ?? 0, field.required ? 1 : 0)}` : ""}{spec.max ? ` · au plus ${spec.max}` : ""}</p>
      </div>
    );

  };
  return <>{render()}{status}</>;
}
