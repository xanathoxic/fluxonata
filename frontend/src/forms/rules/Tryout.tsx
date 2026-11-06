import { useState } from "react";
import { FormIcon } from "../FormIcon";
import { applyRules, describeEffect, evalNode, initialState, isGroup, TOKEN_RE, type Node, type Rule } from "../rules";
import type { FormField } from "../types";

function involved(n: Node, out: Set<string>) {
  if (isGroup(n)) n.items.forEach((i) => involved(i, out));
  else { if (n.field !== undefined) out.add(n.field); for (const m of (n.template ?? "").matchAll(TOKEN_RE)) out.add(m[1] as string); if (n.ref) out.add(n.ref); }
}

/** « Essayer » : type values for the fields the rule reads and see what it decides, without leaving the editor. */
export function Tryout({ rule, field, fields }: { rule: Rule; field: FormField; fields: FormField[] }) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  const keys = new Set<string>();
  involved(rule.when, keys);
  for (const e of [...rule.then, ...(rule.else ?? [])]) { const t = e.do === "text" ? e.text : e.do === "fill" ? e.value : ""; for (const m of t.matchAll(TOKEN_RE)) keys.add(m[1] as string); }
  const shown = fields.filter((f) => keys.has(f.key));
  const holds = evalNode(rule.when, values);
  const state = applyRules({ rules: [rule] }, values, initialState(field));
  const branch = (holds ? rule.then : rule.else) ?? [];
  const set = (k: string, v: unknown) => setValues({ ...values, [k]: v });
  return (
    <section className="rl-try" aria-label="Essayer la règle">
      <h3><FormIcon name="play" size={16} /> Essayer</h3>
      {shown.length === 0 && <p className="hint">Ajoutez une condition pour l'essayer.</p>}
      <div className="rl-tryfields">
        {shown.map((f) => (
          <label key={f.key} className="rl-tryf">
            <span>{f.label || f.key}</span>
            {f.type === "checkbox" ? <input type="checkbox" checked={values[f.key] === true} onChange={(e) => set(f.key, e.target.checked)} />
              : f.options && f.type !== "checkbox_list" ? <select value={String(values[f.key] ?? "")} onChange={(e) => set(f.key, e.target.value)}><option value="">(vide)</option>{f.options.map((o) => <option key={o.value} value={o.value}>{o.label || o.value}</option>)}</select>
              : f.type === "checkbox_list" ? <input placeholder="a, b" value={((values[f.key] as string[]) ?? []).join(", ")} onChange={(e) => set(f.key, e.target.value.split(",").map((v) => v.trim()).filter(Boolean))} />
              : f.type === "number" ? <input type="number" value={String(values[f.key] ?? "")} onChange={(e) => set(f.key, e.target.value === "" ? undefined : Number(e.target.value))} />
              : <input value={String(values[f.key] ?? "")} onChange={(e) => set(f.key, e.target.value)} />}
          </label>
        ))}
      </div>
      <p className={`rl-verdict ${holds ? "yes" : "no"}`} role="status"><FormIcon name={holds ? "check" : "close"} size={14} /> La condition est {holds ? "vraie" : "fausse"}</p>
      <p className="rl-result" role="status">
        {branch.length === 0 ? "Aucun effet dans ce cas." : <>Effets : {branch.map(describeEffect).join(" · ")}</>}
        <br /><small>Résultat : {state.visible ? "affiché" : "masqué"} · {state.required ? "obligatoire" : "facultatif"}{state.readonly ? " · lecture seule" : ""}{state.label !== null ? ` · libellé « ${state.label} »` : ""}{state.help !== null ? ` · aide « ${state.help} »` : ""}{state.placeholder !== null ? ` · exemple « ${state.placeholder} »` : ""}{state.fill !== null ? ` · valeur « ${state.fill} »` : ""}{state.options ? ` · choix : ${state.options.join(", ") || "aucun"}` : ""}</small>
      </p>
    </section>
  );
}
