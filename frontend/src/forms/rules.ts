import { refs as calcRefs } from "./calc";
import type { FormField } from "./types";

/** Rules of a field: conditions on the values of earlier fields and what they change. This file and the server
 * (`triggers/formrules.py`) evaluate them with the same definitions, checked on one shared list of cases
 * (`backend/tests/fixtures/form_rules_*.json`). What decides is the server. */
export type CondOp = "equals" | "not_equals" | "contains" | "not_contains" | "starts_with" | "ends_with" | "in_list" | "includes_any" | "is_empty" | "not_empty" | "is_true" | "is_false" | "gt" | "gte" | "lt" | "lte";
export type Scalar = string | number | boolean;
export interface Cond { field?: string; template?: string; op: CondOp; value?: Scalar; ref?: string; values?: (string | number)[]; ci?: boolean }
export interface Group { op: "all" | "any"; items: Node[] }
export type Node = Cond | Group;
export type TextTarget = "label" | "help" | "placeholder";
export type Effect = { do: "show" } | { do: "hide" } | { do: "require" } | { do: "optional" } | { do: "readonly" } | { do: "editable" } | { do: "text"; target: TextTarget; text: string } | { do: "options"; values: string[] } | { do: "fill"; value: string } | { do: "load"; what: LoadKind };
export interface Rule { id?: string; name?: string; when: Node; then: Effect[]; else?: Effect[] }
/** What a « load » effect lets a custom control read from its service (see triggers/formrules.py). */
export type LoadKind = "options" | "prefill";
export interface State { load: LoadKind[]; visible: boolean; required: boolean; readonly: boolean; label: string | null; help: string | null; placeholder: string | null; options: string[] | null; fill: string | null }

export const isGroup = (n: Node): n is Group => "items" in n;
type Values = Record<string, unknown>;

export function textOf(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return String(v);
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v.map(textOf).join(",");
  return "";
}
const DECIMAL = /^[+-]?(\d+\.?\d*|\.\d+)$/;
const ISO = /^\d{4}-\d{2}-\d{2}$/;
export function numOf(v: unknown): number | null {
  if (typeof v === "boolean") return null;
  if (typeof v === "number") return v;
  if (typeof v === "string" && DECIMAL.test(v.trim())) return Number(v.trim());
  return null;
}
const empty = (v: unknown) => v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0);

export function evalCond(c: Cond, values: Values): boolean {
  const left = c.template !== undefined ? render(c.template, values) : values[c.field as string];
  const fold = c.ci === true ? (s: string) => s.toLowerCase() : (s: string) => s;
  switch (c.op) {
    case "is_empty": return empty(left);
    case "not_empty": return !empty(left);
    case "is_true": return left === true;
    case "is_false": return left === false;
    case "in_list": return (c.values ?? []).map((x) => fold(textOf(x))).includes(fold(textOf(left)));
    case "includes_any": return Array.isArray(left) && left.some((i) => (c.values ?? []).map((x) => fold(textOf(x))).includes(fold(textOf(i))));
  }
  const right = c.ref !== undefined ? values[c.ref] : c.value;
  switch (c.op) {
    case "equals": return fold(textOf(left)) === fold(textOf(right));
    case "not_equals": return fold(textOf(left)) !== fold(textOf(right));
    case "contains":
    case "not_contains": {
      const needle = fold(textOf(right));
      const found = Array.isArray(left) ? left.some((i) => fold(textOf(i)) === needle) : fold(textOf(left)).includes(needle);
      return c.op === "contains" ? found : !found;
    }
    case "starts_with": return fold(textOf(left)).startsWith(fold(textOf(right)));
    case "ends_with": return fold(textOf(left)).endsWith(fold(textOf(right)));
  }
  const nl = numOf(left), nr = numOf(right);
  let a: number | string, b: number | string;
  if (nl !== null && nr !== null) { a = nl; b = nr; }
  else if (typeof left === "string" && typeof right === "string" && ISO.test(left) && ISO.test(right)) { a = left; b = right; }
  else return false;
  return c.op === "gt" ? a > b : c.op === "gte" ? a >= b : c.op === "lt" ? a < b : a <= b;
}

export function evalNode(n: Node, values: Values): boolean {
  if (isGroup(n)) return n.op === "all" ? n.items.every((i) => evalNode(i, values)) : n.items.some((i) => evalNode(i, values));
  return evalCond(n, values);
}

export const TOKEN_RE = /\{([A-Za-z_][A-Za-z0-9_]{0,63})\}/g;
const TOKEN = TOKEN_RE;
export const render = (template: string, values: Values): string => template.replace(TOKEN, (_, k: string) => textOf(values[k]));

export const initialState = (f: Pick<FormField, "required">): State => ({ visible: true, required: Boolean(f.required), readonly: false, label: null, help: null, placeholder: null, options: null, fill: null, load: [] });

export function applyRules(f: Pick<FormField, "rules">, values: Values, state: State): State {
  for (const rule of f.rules ?? []) {
    for (const e of (evalNode(rule.when, values) ? rule.then : rule.else) ?? []) {
      if (e.do === "show" || e.do === "hide") state.visible = e.do === "show";
      else if (e.do === "require" || e.do === "optional") state.required = e.do === "require";
      else if (e.do === "readonly" || e.do === "editable") state.readonly = e.do === "readonly";
      else if (e.do === "text") state[e.target] = render(e.text, values);
      else if (e.do === "fill") state.fill = render(e.value, values);
      else if (e.do === "load") { if (!state.load.includes(e.what)) state.load.push(e.what); }
      else state.options = e.values.map(textOf);
    }
  }
  return state;
}

/** What the field's rules put under their control: a service reading that a « load » effect names loads only while one of those effects
 *  applies; a reading no rule names loads by itself. */
export function gatedLoads(f: Pick<FormField, "rules">): Set<LoadKind> {
  const out = new Set<LoadKind>();
  for (const r of f.rules ?? []) for (const e of [...r.then, ...(r.else ?? [])]) if (e.do === "load") out.add(e.what);
  return out;
}
/** Whether a service reading may load now: always, unless a rule names it and none of the rules that name it applies. */
export const canLoad = (f: Pick<FormField, "rules">, state: Pick<State, "load"> | undefined, what: LoadKind): boolean => !gatedLoads(f).has(what) || Boolean(state?.load.includes(what));

export const canHide = (f: Pick<FormField, "rules">): boolean => (f.rules ?? []).some((r) => [...r.then, ...(r.else ?? [])].some((e) => e.do === "show" || e.do === "hide"));

// ---- wording and the operators each kind of field offers (used by the condition editor) ------------------------------------
export interface OpInfo { id: CondOp; label: string; unary?: boolean; list?: boolean }
const O = (id: CondOp, label: string, extra: Partial<OpInfo> = {}): OpInfo => ({ id, label, ...extra });
const TEXTY: OpInfo[] = [O("equals", "est égal à"), O("not_equals", "est différent de"), O("contains", "contient"), O("not_contains", "ne contient pas"), O("starts_with", "commence par"), O("ends_with", "finit par"), O("in_list", "est l'une de", { list: true }), O("is_empty", "est vide", { unary: true }), O("not_empty", "n'est pas vide", { unary: true })];
const ORDER: OpInfo[] = [O("gt", "est supérieur à"), O("gte", "est supérieur ou égal à"), O("lt", "est inférieur à"), O("lte", "est inférieur ou égal à")];
export function operatorsFor(type: string): OpInfo[] {
  if (type === "checkbox") return [O("is_true", "est coché", { unary: true }), O("is_false", "n'est pas coché", { unary: true })];
  if (type === "composed") return [...TEXTY.slice(0, 6), ...ORDER, ...TEXTY.slice(6)];
  if (type === "custom") return [O("is_empty", "est vide", { unary: true }), O("not_empty", "est renseigné", { unary: true })];
  if (type === "checkbox_list") return [O("contains", "contient"), O("not_contains", "ne contient pas"), O("includes_any", "contient l'un de", { list: true }), O("is_empty", "est vide", { unary: true }), O("not_empty", "n'est pas vide", { unary: true })];
  if (type === "datagrid") return [O("is_empty", "est vide", { unary: true }), O("not_empty", "n'est pas vide", { unary: true })];
  if (type === "number") return [TEXTY[0] as OpInfo, TEXTY[1] as OpInfo, ...ORDER, TEXTY[6] as OpInfo, TEXTY[7] as OpInfo, TEXTY[8] as OpInfo];
  if (type === "date") return [TEXTY[0] as OpInfo, TEXTY[1] as OpInfo, ...ORDER, TEXTY[7] as OpInfo, TEXTY[8] as OpInfo];
  return TEXTY;
}
export const opLabel = (op: CondOp): string => [...TEXTY, ...ORDER, ...operatorsFor("checkbox"), ...operatorsFor("checkbox_list")].find((o) => o.id === op)?.label ?? op;

const q = (v: unknown) => (typeof v === "string" ? `« ${v} »` : String(v));
/** A condition tree as a sentence: « Pays est égal à « FR » ET (Âge est supérieur ou égal à 18 OU … ) ». */
export function sentence(n: Node, label: (key: string) => string, depth = 0): string {
  if (isGroup(n)) {
    const parts = n.items.map((i) => sentence(i, label, depth + 1));
    const text = parts.join(n.op === "all" ? " ET " : " OU ");
    return depth > 0 && parts.length > 1 ? `(${text})` : text;
  }
  const info = operatorsFor("text").concat(ORDER, operatorsFor("checkbox"), operatorsFor("checkbox_list")).find((o) => o.id === n.op);
  const left = n.template !== undefined ? `la valeur composée « ${n.template.replace(TOKEN_RE, (_, k: string) => `{${label(k)}}`)} »` : label(n.field as string);
  const ci = n.ci ? " (sans tenir compte de la casse)" : "";
  if (info?.unary) return `${left} ${info.label}`;
  if (info?.list) return `${left} ${info.label} ${(n.values ?? []).map(q).join(", ")}${ci}`;
  const right = n.ref !== undefined ? `la valeur de « ${label(n.ref)} »` : q(n.value ?? "");
  return `${left} ${info?.label ?? n.op} ${right}${ci}`;
}
export const describeEffect = (e: Effect): string => {
  switch (e.do) {
    case "show": return "afficher le champ";
    case "hide": return "masquer le champ";
    case "require": return "le rendre obligatoire";
    case "optional": return "le rendre facultatif";
    case "readonly": return "le mettre en lecture seule";
    case "editable": return "le rendre modifiable";
    case "text": return `${{ label: "libellé", help: "aide", placeholder: "texte d'exemple" }[e.target]} : « ${e.text} »`;
    case "fill": return `remplir le champ avec « ${e.value} »`;
    case "load": return e.what === "options" ? "charger les options depuis le service" : "précharger la valeur depuis le service";
    default: return `ne proposer que ${e.values.map((v) => `« ${v} »`).join(", ")}`;
  }
};

/** Keys of the fields this field reads: its rules (conditions, compared fields, {tokens}) and its legacy visibility expression. */
export function reads(f: Pick<FormField, "rules" | "visible_if" | "compute">): Set<string> {
  const out = new Set<string>();
  const walk = (n: Node) => { if (isGroup(n)) n.items.forEach(walk); else { if (n.field !== undefined) out.add(n.field); for (const m of (n.template ?? "").matchAll(TOKEN_RE)) out.add(m[1] as string); if (n.ref !== undefined) out.add(n.ref); } };
  for (const r of f.rules ?? []) {
    walk(r.when);
    for (const e of [...r.then, ...(r.else ?? [])]) {
      const t = e.do === "text" ? e.text : e.do === "fill" ? e.value : "";
      for (const m of t.matchAll(TOKEN_RE)) out.add(m[1] as string);
    }
  }
  for (const k of calcRefs(f.compute)) out.add(k);
  if (f.visible_if) for (const m of f.visible_if.matchAll(/values\.([A-Za-z_][A-Za-z0-9_]*)/g)) out.add(m[1] as string);
  return out;
}

/** Fields so that each comes after the ones it reads (ties: reading order), like the server. */
export function evaluationOrder<T extends Pick<FormField, "key" | "rules" | "visible_if" | "compute">>(fields: T[]): T[] {
  const keys = new Set(fields.map((f) => f.key));
  const deps = new Map(fields.map((f) => [f.key, [...reads(f)].filter((k) => keys.has(k))]));
  const done = new Set<string>();
  const left = [...fields];
  const out: T[] = [];
  while (left.length) {
    const i = Math.max(0, left.findIndex((f) => (deps.get(f.key) ?? []).every((k) => done.has(k))));
    const [next] = left.splice(i, 1) as [T];
    done.add(next.key);
    out.push(next);
  }
  return out;
}

/** Fields that read `key`, directly or through others: reading one of them from `key` would make a loop. */
export function readers<T extends Pick<FormField, "key" | "rules" | "visible_if" | "compute">>(fields: T[], key: string): Set<string> {
  const found = new Set<string>();
  const queue = [key];
  while (queue.length) {
    const at = queue.pop() as string;
    for (const f of fields) if (!found.has(f.key) && reads(f).has(at)) { found.add(f.key); queue.push(f.key); }
  }
  return found;
}

/** What a « fill » effect gives to a field, converted like the server (number parsed, box ticked for « true / vrai / oui / 1 »). */
export function fillValue(type: FormField["type"], text: string): unknown {
  if (type === "checkbox") return ["true", "vrai", "oui", "1"].includes(text.trim().toLowerCase());
  if (type === "number") {
    const n = Number(text.trim().replace(",", "."));
    return text.trim() === "" || !Number.isFinite(n) ? text : n;
  }
  return text;
}
