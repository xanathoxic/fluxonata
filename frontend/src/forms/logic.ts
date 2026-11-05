import { dataFields, orderedFields } from "./layout";
import { evaluate, valueFor } from "./calc";
import { processControl } from "./controls";
import { applyRules, evaluationOrder, fillValue, initialState, textOf, type State } from "./rules";
import { isBlock, type FormDefinition, type FormField } from "./types";

/** Visibility rules use the workflow expression language over `values.<key>`. The browser understands the subset the
 * builder writes (comparisons, and / or / not, parentheses, lists); the server is the judge and drops hidden values. */
type Tok = { t: "num" | "str" | "op" | "id" | "p"; v: string };
const TOKEN = /\s*(?:(\d+(?:\.\d+)?)|('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")|(==|!=|<=|>=|<|>)|([A-Za-z_][A-Za-z0-9_]*)|([().[\],]))/y;

function tokenize(src: string): Tok[] {
  const out: Tok[] = [];
  TOKEN.lastIndex = 0;
  while (TOKEN.lastIndex < src.length) {
    if (/^\s*$/.test(src.slice(TOKEN.lastIndex))) break;
    const m = TOKEN.exec(src);
    if (!m) throw new Error("token");
    if (m[1] !== undefined) out.push({ t: "num", v: m[1] });
    else if (m[2] !== undefined) out.push({ t: "str", v: m[2] });
    else if (m[3] !== undefined) out.push({ t: "op", v: m[3] });
    else if (m[4] !== undefined) out.push({ t: "id", v: m[4] });
    else out.push({ t: "p", v: m[5] as string });
  }
  return out;
}

const unquote = (s: string): string => s.slice(1, -1).replace(/\\(.)/g, "$1");

class Evaluator {
  private i = 0;
  constructor(private toks: Tok[], private values: Record<string, unknown>, private known: Set<string>) {}
  private peek = (): Tok | undefined => this.toks[this.i];
  private isWord = (w: string): boolean => this.peek()?.t === "id" && this.peek()?.v === w;
  run(): unknown {
    const v = this.or();
    if (this.i < this.toks.length) throw new Error("trailing");
    return v;
  }
  private or(): unknown {
    let l = this.and();
    while (this.isWord("or")) { this.i++; const r = this.and(); l = Boolean(l) || Boolean(r); }
    return l;
  }
  private and(): unknown {
    let l = this.not();
    while (this.isWord("and")) { this.i++; const r = this.not(); l = Boolean(l) && Boolean(r); }
    return l;
  }
  private not(): unknown {
    if (this.isWord("not")) { this.i++; return !this.not(); }
    return this.cmp();
  }
  private cmp(): unknown {
    const a = this.atom();
    const t = this.peek();
    if (!t || !((t.t === "op") || (t.t === "id" && (t.v === "in" || t.v === "contains")))) return a;
    this.i++;
    const b = this.atom();
    switch (t.v) {
      case "==": return a === b;
      case "!=": return a !== b;
      case "in": if (!Array.isArray(b)) throw new Error("in"); return b.includes(a);
      case "contains":
        if (typeof a === "string" && typeof b === "string") return a.includes(b);
        if (Array.isArray(a)) return a.includes(b);
        throw new Error("contains");
      default: {
        const ok = (typeof a === "number" && typeof b === "number") || (typeof a === "string" && typeof b === "string");
        if (!ok) throw new Error("compare");
        const x = a as number, y = b as number;
        return t.v === "<" ? x < y : t.v === "<=" ? x <= y : t.v === ">" ? x > y : x >= y;
      }
    }
  }
  private atom(): unknown {
    const t = this.toks[this.i++];
    if (!t) throw new Error("eof");
    if (t.t === "num") return Number(t.v);
    if (t.t === "str") return unquote(t.v);
    if (t.t === "p" && t.v === "(") { const v = this.or(); this.expect(")"); return v; }
    if (t.t === "p" && t.v === "[") {
      const items: unknown[] = [];
      while (this.peek()?.v !== "]") { items.push(this.atom()); if (this.peek()?.v === ",") this.i++; else break; }
      this.expect("]");
      return items;
    }
    if (t.t === "id") {
      if (t.v === "true") return true;
      if (t.v === "false") return false;
      if (t.v === "null") return null;
      if (t.v !== "values") throw new Error("path");
      this.expect(".");
      const key = this.toks[this.i++];
      if (!key || key.t !== "id" || !this.known.has(key.v)) throw new Error("unknown");
      const v = this.values[key.v];
      return v === undefined ? null : v;
    }
    throw new Error("atom");
  }
  private expect(p: string) { if (this.toks[this.i++]?.v !== p) throw new Error("expect"); }
}

/** True when the rule holds. An unreadable rule or situation hides the field, like the server does. */
export function ruleHolds(expr: string, values: Record<string, unknown>, known: Set<string>): boolean {
  try { return Boolean(new Evaluator(tokenize(expr), values, known).run()); } catch { return false; }
}

/** Fields shown for these values, and the values the server would keep (a hidden field's value is dropped). */
export function visibleState(def: FormDefinition, values: Record<string, unknown>): { shown: Set<string>; kept: Record<string, unknown>; states: Map<string, State> } {
  const shown = new Set<string>();
  const kept: Record<string, unknown> = {};
  const states = new Map<string, State>();
  const all = orderedFields(def);
  const known = new Set(all.filter((f) => !isBlock(f.type)).map((f) => f.key));
  // a field's rules read the values kept so far; the fields it reads come first, wherever they are laid out (like the server)
  for (const f of evaluationOrder(all)) {
    const state = applyRules(f, kept, initialState(f));
    states.set(f.key, state);
    if (state.visible && (!f.visible_if || ruleHolds(f.visible_if, kept, known))) {
      shown.add(f.key);
      if (!isBlock(f.type) && state.fill !== null) kept[f.key] = fillValue(f.type, state.fill);
      else if (!isBlock(f.type) && f.compute) {  // calculated: the formula's result, never what was typed
        const v = valueFor(f.type, evaluate(f.compute, kept));
        if (v !== null) kept[f.key] = v;
      } else if (!isBlock(f.type) && f.key in values) {
        kept[f.key] = values[f.key];
        if (f.type === "custom") { const r = processControl(f, values[f.key]); if (!r.problem) kept[f.key] = r.value; }  // else keep what was typed: the check reports it
      }
    }
  }
  return { shown, kept, states };
}

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
export const isEmpty = (v: unknown) => v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0) || (typeof v === "object" && v !== null && !Array.isArray(v) && Object.keys(v).length === 0);

/** Same checks as the server, for the visible fields only (the server stays the judge). */
export function validateDefinition(def: FormDefinition, values: Record<string, unknown>): Record<string, string> {
  const { shown, kept, states } = visibleState(def, values);
  const errors: Record<string, string> = {};
  for (const f of dataFields(def)) {
    if (!shown.has(f.key)) continue;
    const state = states.get(f.key) as State;
    const v = kept[f.key];
    if (isEmpty(v)) { if (state.required) errors[f.key] = "Ce champ est obligatoire."; continue; }
    const e = fieldError(f, v) || (state.options && (Array.isArray(v) ? v : [v]).some((x) => !(state.options as string[]).includes(textOf(x))) ? "Choix non proposé." : "");
    if (e) errors[f.key] = e;
  }
  return errors;
}

function fieldError(f: FormField, v: unknown): string {
  if (f.type === "custom") return processControl(f, v).problem;
  if (f.type === "email" && !EMAIL.test(String(v))) return "Adresse e-mail invalide.";
  if (f.type === "date" && !/^\d{4}-\d{2}-\d{2}$/.test(String(v))) return "Date invalide (AAAA-MM-JJ).";
  if (f.type === "number") {
    if (typeof v !== "number" || Number.isNaN(v)) return "Un nombre est attendu.";
    if (f.min !== undefined && v < f.min) return `Au moins ${f.min}.`;
    if (f.max !== undefined && v > f.max) return `Au plus ${f.max}.`;
  }
  if (f.options && (f.type === "select" || f.type === "radio" || f.type === "combobox") && !f.options.some((o) => o.value === v)) return "Choix non proposé.";
  return "";
}

// ---- the builder's structured rules <-> expression text -------------------------------------------------------
export type Op = "==" | "!=" | "<" | "<=" | ">" | ">=" | "contains";
export const OPS: { op: Op; label: string }[] = [
  { op: "==", label: "est égal à" }, { op: "!=", label: "est différent de" }, { op: "contains", label: "contient" },
  { op: ">", label: "est supérieur à" }, { op: ">=", label: "est supérieur ou égal à" }, { op: "<", label: "est inférieur à" }, { op: "<=", label: "est inférieur ou égal à" },
];
export interface Rule { field: string; op: Op; value: string | number | boolean }
export interface RuleSet { join: "and" | "or"; rules: Rule[] }

const quote = (s: string) => `'${s.replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;
const literal = (v: Rule["value"]) => (typeof v === "string" ? quote(v) : String(v));

export function ruleSetToExpr(rs: RuleSet): string {
  return rs.rules.map((r) => `values.${r.field} ${r.op} ${literal(r.value)}`).join(` ${rs.join} `);
}

/** Reads back what `ruleSetToExpr` wrote; null for anything more elaborate (the builder then shows the text). */
export function exprToRuleSet(expr: string): RuleSet | null {
  try {
    const toks = tokenize(expr);
    const rules: Rule[] = [];
    let join: "and" | "or" | null = null;
    let i = 0;
    while (i < toks.length) {
      const [a, dot, key, op, lit] = toks.slice(i, i + 5);
      if (a?.v !== "values" || dot?.v !== "." || key?.t !== "id" || !op || !lit) return null;
      if (!(["==", "!=", "<", "<=", ">", ">="].includes(op.v) || op.v === "contains")) return null;
      let value: Rule["value"];
      if (lit.t === "num") value = Number(lit.v);
      else if (lit.t === "str") value = unquote(lit.v);
      else if (lit.t === "id" && (lit.v === "true" || lit.v === "false")) value = lit.v === "true";
      else return null;
      rules.push({ field: key.v, op: op.v as Op, value });
      i += 5;
      if (i < toks.length) {
        const j = toks[i]?.v;
        if (j !== "and" && j !== "or") return null;
        if (join && join !== j) return null;
        join = j;
        i++;
        if (i >= toks.length) return null;
      }
    }
    return rules.length ? { join: join ?? "and", rules } : null;
  } catch { return null; }
}
