import { FormIcon } from "../FormIcon";
import { isGroup, operatorsFor, type Cond, type CondOp, type Group, type Node } from "../rules";
import type { FormField } from "../types";

export const MAX_DEPTH = 4;
const COMPOSED = "__composed";
const CI_OPS = new Set<CondOp>(["equals", "not_equals", "contains", "not_contains", "starts_with", "ends_with", "in_list", "includes_any"]);

/** A condition with only the operand its operator takes (the server refuses anything else). */
export function shape(c: Cond): Cond {
  const info = operatorsFor("text").concat(operatorsFor("checkbox"), operatorsFor("checkbox_list"), operatorsFor("number")).find((o) => o.id === c.op);
  const out: Cond = c.template !== undefined ? { template: c.template, op: c.op } : { field: c.field, op: c.op };
  if (c.ci && CI_OPS.has(c.op)) out.ci = true;
  if (info?.unary) return out;
  if (info?.list) { out.values = c.values ?? (c.value !== undefined && c.value !== "" ? [String(c.value)] : []); return out; }
  if (c.ref !== undefined) out.ref = c.ref; else out.value = c.value ?? "";
  return out;
}

export function newCond(fields: FormField[]): Cond {
  const f = fields[0] as FormField;
  return shape({ field: f.key, op: operatorsFor(f.type)[0]?.id ?? "equals" });
}

interface TreeProps { group: Group; fields: FormField[]; path: number[]; onChange: (g: Group) => void; onRemove?: () => void }

/** A group of conditions (all of them, or at least one), which may hold other groups. */
export function GroupEditor({ group, fields, path, onChange, onRemove }: TreeProps) {
  const depth = path.length;
  const set = (items: Node[]) => onChange({ ...group, items });
  return (
    <div className={`rl-group d${Math.min(depth, 3)}`} role="group" aria-label={depth === 0 ? "Conditions" : `Groupe ${path.map((p) => p + 1).join(".")}`}>
      <div className="rl-ghead">
        <span className="rl-seg" role="radiogroup" aria-label={`Combinaison${depth ? ` du groupe ${path.map((p) => p + 1).join(".")}` : ""}`}>
          <button type="button" role="radio" aria-checked={group.op === "all"} className={group.op === "all" ? "on" : ""} title="Toutes les conditions doivent être vraies" onClick={() => onChange({ ...group, op: "all" })}>ET</button>
          <button type="button" role="radio" aria-checked={group.op === "any"} className={group.op === "any" ? "on" : ""} title="Au moins une condition doit être vraie" onClick={() => onChange({ ...group, op: "any" })}>OU</button>
        </span>
        <span className="hint">{group.op === "all" ? "toutes les conditions" : "au moins une condition"}</span>
        <span className="fb-spacer" />
        <button type="button" className="rl-small" onClick={() => set([...group.items, newCond(fields)])} disabled={fields.length === 0}><FormIcon name="plus" size={14} /> Condition</button>
        {depth < MAX_DEPTH && <button type="button" className="rl-small" onClick={() => set([...group.items, { op: group.op === "all" ? "any" : "all", items: [newCond(fields)] }])} disabled={fields.length === 0}><FormIcon name="layers" size={14} /> Groupe</button>}
        {onRemove && <button type="button" className="fb-ibtn fb-del rl-x" aria-label={`Supprimer le groupe ${path.map((p) => p + 1).join(".")}`} title="Supprimer ce groupe" onClick={onRemove}><FormIcon name="trash" size={14} /></button>}
      </div>
      <div className="rl-items">
        {group.items.map((item, i) => {
          const at = [...path, i];
          const replace = (n: Node) => set(group.items.map((x, k) => (k === i ? n : x)));
          const remove = () => set(group.items.filter((_, k) => k !== i));
          return isGroup(item)
            ? <GroupEditor key={i} group={item} fields={fields} path={at} onChange={replace} onRemove={remove} />
            : <CondRow key={i} cond={item} fields={fields} label={`Condition ${at.map((p) => p + 1).join(".")}`} onChange={replace} onRemove={group.items.length > 1 || depth > 0 ? remove : undefined} />;
        })}
      </div>
    </div>
  );
}

function CondRow({ cond, fields, label, onChange, onRemove }: { cond: Cond; fields: FormField[]; label: string; onChange: (c: Cond) => void; onRemove?: () => void }) {
  const composed = cond.template !== undefined;
  const src = composed ? undefined : fields.find((f) => f.key === cond.field);
  const ops = operatorsFor(composed ? "composed" : (src?.type ?? "text"));
  const info = ops.find((o) => o.id === cond.op);
  const others = fields.filter((f) => f.key !== cond.field);
  const setField = (key: string) => {
    if (key === COMPOSED) {
      const keep = operatorsFor("composed").some((o) => o.id === cond.op);
      onChange(shape({ template: `{${(fields[0] as FormField).key}}`, op: keep ? cond.op : "equals", ...(keep ? { ci: cond.ci, value: cond.value, ref: cond.ref, values: cond.values } : {}) }));
      return;
    }
    const f = fields.find((x) => x.key === key) as FormField;
    const available = operatorsFor(f.type);
    onChange(shape({ field: key, op: available.some((o) => o.id === cond.op) ? cond.op : (available[0] as { id: CondOp }).id, ci: cond.ci, ...(available.some((o) => o.id === cond.op) ? { value: cond.value, ref: cond.ref, values: cond.values } : {}) }));
  };
  const setOp = (op: CondOp) => onChange(shape({ ...cond, op }));
  const mode = cond.ref !== undefined ? "ref" : "value";
  return (
    <div className="rl-cond" role="group" aria-label={label}>
      <select aria-label={`${label} : champ`} value={composed ? COMPOSED : cond.field} onChange={(e) => setField(e.target.value)}>
        {!src && !composed && <option value={cond.field}>{cond.field} (introuvable)</option>}
        {fields.map((f) => <option key={f.key} value={f.key}>{f.label || f.key}</option>)}
        <option value={COMPOSED}>Valeur composée…</option>
      </select>
      {composed && (
        <>
          <input aria-label={`${label} : valeur composée`} className="rl-composed" value={cond.template} maxLength={500} placeholder="ex. {prenom} {nom}" onChange={(e) => onChange(shape({ ...cond, template: e.target.value }))} />
          <select aria-label={`${label} : insérer un champ`} value="" onChange={(e) => e.target.value && onChange(shape({ ...cond, template: `${cond.template}{${e.target.value}}` }))}>
            <option value="">Insérer {"{champ}"}…</option>
            {fields.map((f) => <option key={f.key} value={f.key}>{f.label || f.key}</option>)}
          </select>
        </>
      )}
      <select aria-label={`${label} : opérateur`} value={cond.op} onChange={(e) => setOp(e.target.value as CondOp)}>
        {ops.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>
      {info && !info.unary && !info.list && (
        <span className="rl-mode" role="radiogroup" aria-label={`${label} : comparer à`}>
          <button type="button" role="radio" aria-checked={mode === "value"} className={mode === "value" ? "on" : ""} aria-label="Une valeur" title="Comparer à une valeur" onClick={() => onChange(shape({ ...cond, ref: undefined, value: cond.value ?? "" }))}><FormIcon name="text" size={14} /></button>
          <button type="button" role="radio" aria-checked={mode === "ref"} className={mode === "ref" ? "on" : ""} aria-label="La valeur d'un autre champ" title="Comparer à la valeur d'un autre champ" disabled={others.length === 0} onClick={() => onChange(shape({ ...cond, value: undefined, ref: others[0]?.key }))}><FormIcon name="link" size={14} /></button>
        </span>
      )}
      {info && !info.unary && (info.list
        ? <ListOperand cond={cond} src={src} label={label} onChange={onChange} />
        : mode === "ref"
          ? <select aria-label={`${label} : autre champ`} value={cond.ref} onChange={(e) => onChange(shape({ ...cond, ref: e.target.value }))}>{others.map((f) => <option key={f.key} value={f.key}>{f.label || f.key}</option>)}</select>
          : <ValueInput cond={cond} src={src} label={label} onChange={onChange} />)}
      {CI_OPS.has(cond.op) && src?.type !== "number" && src?.type !== "date" && (
        <button type="button" className={`rl-ci${cond.ci ? " on" : ""}`} aria-pressed={Boolean(cond.ci)} aria-label={`${label} : ignorer la casse`} title="Ignorer la casse (majuscules et minuscules)" onClick={() => onChange(shape({ ...cond, ci: !cond.ci }))}>Aa</button>
      )}
      {onRemove && <button type="button" className="fb-ibtn fb-del rl-x" aria-label={`Supprimer ${label.toLowerCase()}`} title="Supprimer cette condition" onClick={onRemove}><FormIcon name="trash" size={14} /></button>}
    </div>
  );
}

function ValueInput({ cond, src, label, onChange }: { cond: Cond; src?: FormField; label: string; onChange: (c: Cond) => void }) {
  const value = cond.value ?? "";
  const set = (v: string | number) => onChange(shape({ ...cond, value: v }));
  if (src?.options && (cond.op === "equals" || cond.op === "not_equals"))
    return <select aria-label={`${label} : valeur`} value={String(value)} onChange={(e) => set(e.target.value)}><option value="">Choisir…</option>{src.options.map((o) => <option key={o.value} value={o.value}>{o.label || o.value}</option>)}</select>;
  if (src?.type === "number") return <input type="number" aria-label={`${label} : valeur`} value={typeof value === "number" ? value : String(value)} onChange={(e) => set(e.target.value === "" ? "" : Number(e.target.value))} />;
  if (src?.type === "date") return <input type="date" aria-label={`${label} : valeur`} value={String(value)} onChange={(e) => set(e.target.value)} />;
  return <input aria-label={`${label} : valeur`} value={String(value)} maxLength={500} placeholder="valeur" onChange={(e) => set(e.target.value)} />;
}

function ListOperand({ cond, src, label, onChange }: { cond: Cond; src?: FormField; label: string; onChange: (c: Cond) => void }) {
  const values = (cond.values ?? []).map(String);
  if (src?.options)
    return (
      <span className="rl-chips" role="group" aria-label={`${label} : valeurs`}>
        {src.options.map((o) => {
          const on = values.includes(o.value);
          return <button key={o.value} type="button" aria-pressed={on} className={`rl-chip${on ? " on" : ""}`} onClick={() => onChange(shape({ ...cond, values: on ? values.filter((v) => v !== o.value) : [...values, o.value] }))}>{o.label || o.value}</button>;
        })}
      </span>
    );
  return <input aria-label={`${label} : valeurs (séparées par des virgules)`} value={values.join(", ")} placeholder="a, b, c" onChange={(e) => onChange(shape({ ...cond, values: e.target.value.split(",").map((v) => v.trim()).filter(Boolean) }))} />;
}
