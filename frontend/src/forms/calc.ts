import { numOf, textOf } from "./rules";
import type { FormField } from "./types";

/** Calculated fields: the same formula language as the server (`backend/triggers/formcalc.py`), tested on the same cases.
 * An operand that makes no sense (an empty field in a sum, a text in a product, a division by zero) gives no value (null). */
export const MAX_FORMULA = 500;
const MAX_NODES = 100;
const MAX_DEPTH = 20;
const MAX_TEXT = 2000;
const LIMIT = 1e15;
export const COMPUTABLE: FormField["type"][] = ["text", "textarea", "number", "email", "date"];

const ARITY: Record<string, [number, number | null]> = {
  if: [3, 3], and: [1, null], or: [1, null], not: [1, 1], round: [1, 2], floor: [1, 1], ceil: [1, 1], abs: [1, 1], min: [1, null], max: [1, null],
  sum: [1, null], avg: [1, null], len: [1, 1], upper: [1, 1], lower: [1, 1], trim: [1, 1], concat: [1, null], left: [2, 2], right: [2, 2],
  number: [1, 1], text: [1, 1], is_empty: [1, 1], coalesce: [1, null], days_between: [2, 2], count: [1, 1], sum_col: [2, 2],
};

/** The functions offered by the editor, with what they do and an example. */
export const FUNCTION_DOCS: { group: string; name: string; usage: string; help: string; example: string }[] = [
  { group: "Nombres", name: "round", usage: "round(x, décimales)", help: "Arrondit (au plus proche, 0 à 10 décimales).", example: "round({ttc} / 1.2, 2)" },
  { group: "Nombres", name: "floor", usage: "floor(x)", help: "Arrondit à l'entier inférieur.", example: "floor({montant})" },
  { group: "Nombres", name: "ceil", usage: "ceil(x)", help: "Arrondit à l'entier supérieur.", example: "ceil({montant})" },
  { group: "Nombres", name: "abs", usage: "abs(x)", help: "Valeur absolue.", example: "abs({ecart})" },
  { group: "Nombres", name: "min", usage: "min(a, b, …)", help: "Le plus petit ; les champs vides sont ignorés.", example: "min({a}, {b})" },
  { group: "Nombres", name: "max", usage: "max(a, b, …)", help: "Le plus grand ; les champs vides sont ignorés.", example: "max({a}, {b})" },
  { group: "Nombres", name: "sum", usage: "sum(a, b, …)", help: "La somme ; les champs vides comptent pour rien.", example: "sum({a}, {b}, {c})" },
  { group: "Nombres", name: "avg", usage: "avg(a, b, …)", help: "La moyenne des valeurs renseignées.", example: "avg({n1}, {n2})" },
  { group: "Nombres", name: "number", usage: "number(x)", help: "Lit un texte comme un nombre.", example: "number({code}) + 1" },
  { group: "Texte", name: "concat", usage: "concat(a, b, …)", help: "Met bout à bout (comme l'opérateur &).", example: "concat({ville}, \" (\", {code}, \")\")" },
  { group: "Texte", name: "upper", usage: "upper(t)", help: "En majuscules.", example: "upper({nom})" },
  { group: "Texte", name: "lower", usage: "lower(t)", help: "En minuscules.", example: "lower({email})" },
  { group: "Texte", name: "trim", usage: "trim(t)", help: "Retire les espaces au début et à la fin.", example: "trim({nom})" },
  { group: "Texte", name: "len", usage: "len(t)", help: "Nombre de caractères.", example: "len({commentaire})" },
  { group: "Texte", name: "left", usage: "left(t, n)", help: "Les n premiers caractères.", example: "left({nom}, 3)" },
  { group: "Texte", name: "right", usage: "right(t, n)", help: "Les n derniers caractères.", example: "right({code}, 2)" },
  { group: "Texte", name: "text", usage: "text(x)", help: "Convertit en texte.", example: "text({n}) & \" lignes\"" },
  { group: "Logique", name: "if", usage: "if(condition, alors, sinon)", help: "Choisit une valeur selon une condition (= <> < <= > >=).", example: "if({age} >= 18, \"majeur\", \"mineur\")" },
  { group: "Logique", name: "and", usage: "and(a, b, …)", help: "Vrai si tout est vrai.", example: "and({a} > 1, {b} > 1)" },
  { group: "Logique", name: "or", usage: "or(a, b, …)", help: "Vrai si l'un est vrai.", example: "or({a} = \"x\", {b} = \"x\")" },
  { group: "Logique", name: "not", usage: "not(a)", help: "Inverse.", example: "not(is_empty({a}))" },
  { group: "Logique", name: "is_empty", usage: "is_empty(x)", help: "Vrai si le champ est vide.", example: "is_empty({a})" },
  { group: "Logique", name: "coalesce", usage: "coalesce(a, b, …)", help: "La première valeur renseignée.", example: "coalesce({surnom}, {prenom})" },
  { group: "Contrôles", name: "count", usage: "count({contrôle})", help: "Nombre de lignes (ou de choix cochés) d'un contrôle personnalisé.", example: "count({lignes})" },
  { group: "Contrôles", name: "sum_col", usage: "sum_col({contrôle}, \"colonne\")", help: "Total d'une colonne d'un contrôle à lignes répétées.", example: "sum_col({lignes}, \"total\")" },
  { group: "Dates", name: "days_between", usage: "days_between(début, fin)", help: "Nombre de jours entre deux dates (AAAA-MM-JJ).", example: "days_between({debut}, {fin})" },
];

type Node =
  | { k: "num"; v: number } | { k: "str"; v: string } | { k: "bool"; v: boolean } | { k: "ref"; v: string }
  | { k: "neg"; a: Node } | { k: "bin"; op: string; a: Node; b: Node } | { k: "call"; name: string; args: Node[] };
type Tok = { t: "num" | "str" | "ref" | "id" | "op"; v: string | number };

const TOKEN = /\s*(?:(\d+(?:\.\d+)?)|"([^"]*)"|'([^']*)'|\{([A-Za-z_][A-Za-z0-9_]{0,63})\}|([A-Za-z_][A-Za-z0-9_]*)|(<=|>=|<>|!=|[=<>+\-*/%&(),]))/y;

export class FormulaError extends Error {}

function tokenize(text: string): Tok[] {
  const out: Tok[] = [];
  let pos = 0;
  while (pos < text.length) {
    if (text.slice(pos).trim() === "") break;
    TOKEN.lastIndex = pos;
    const m = TOKEN.exec(text);
    if (!m || TOKEN.lastIndex === pos) throw new FormulaError(`Caractère inattendu près de « ${text.slice(pos, pos + 12).trim()} ».`);
    pos = TOKEN.lastIndex;
    if (m[1] !== undefined) out.push({ t: "num", v: Number(m[1]) });
    else if (m[2] !== undefined) out.push({ t: "str", v: m[2] });
    else if (m[3] !== undefined) out.push({ t: "str", v: m[3] });
    else if (m[4] !== undefined) out.push({ t: "ref", v: m[4] });
    else if (m[5] !== undefined) out.push({ t: "id", v: m[5] });
    else out.push({ t: "op", v: m[6] as string });
  }
  return out;
}

class Parser {
  i = 0; nodes = 0; depth = 0;
  constructor(private toks: Tok[]) {}
  private peek() { return this.toks[this.i]; }
  private op(...ops: string[]): string | null {
    const t = this.peek();
    if (t && t.t === "op" && ops.includes(t.v as string)) { this.i++; return t.v as string; }
    return null;
  }
  private node<T extends Node>(n: T): T {
    if (++this.nodes > MAX_NODES) throw new FormulaError(`Formule trop longue (${MAX_NODES} éléments au plus).`);
    return n;
  }
  parse(): Node {
    const n = this.expr();
    if (this.peek() !== undefined) throw new FormulaError("Texte inattendu après la fin de la formule.");
    return n;
  }
  private expr(): Node {
    if (++this.depth > MAX_DEPTH) throw new FormulaError("Parenthèses trop imbriquées.");
    const n = this.cmp();
    this.depth--;
    return n;
  }
  private cmp(): Node {
    let left = this.concat();
    for (let op = this.op("=", "<>", "!=", "<=", ">=", "<", ">"); op; op = this.op("=", "<>", "!=", "<=", ">=", "<", ">")) left = this.node({ k: "bin", op: op === "!=" ? "<>" : op, a: left, b: this.concat() });
    return left;
  }
  private concat(): Node {
    let left = this.add();
    while (this.op("&")) left = this.node({ k: "bin", op: "&", a: left, b: this.add() });
    return left;
  }
  private add(): Node {
    let left = this.mul();
    for (let op = this.op("+", "-"); op; op = this.op("+", "-")) left = this.node({ k: "bin", op, a: left, b: this.mul() });
    return left;
  }
  private mul(): Node {
    let left = this.unary();
    for (let op = this.op("*", "/", "%"); op; op = this.op("*", "/", "%")) left = this.node({ k: "bin", op, a: left, b: this.unary() });
    return left;
  }
  private unary(): Node { return this.op("-") ? this.node({ k: "neg", a: this.unary() }) : this.primary(); }
  private primary(): Node {
    const t = this.toks[this.i++];
    if (!t) throw new FormulaError("La formule se termine trop tôt.");
    if (t.t === "num") return this.node({ k: "num", v: t.v as number });
    if (t.t === "str") return this.node({ k: "str", v: t.v as string });
    if (t.t === "ref") return this.node({ k: "ref", v: t.v as string });
    if (t.t === "id") {
      const name = t.v as string;
      if (name === "true" || name === "false") return this.node({ k: "bool", v: name === "true" });
      if (!this.op("(")) throw new FormulaError(`« ${name} » n'est pas connu : un champ s'écrit {champ}, un texte entre guillemets.`);
      const arity = ARITY[name];
      if (!arity) throw new FormulaError(`La fonction « ${name} » n'existe pas.`);
      const args: Node[] = [];
      if (!this.op(")")) {
        for (;;) {
          args.push(this.expr());
          if (this.op(")")) break;
          if (!this.op(",")) throw new FormulaError(`« , » ou « ) » attendu dans ${name}(…).`);
        }
      }
      const [low, high] = arity;
      if (args.length < low || (high !== null && args.length > high)) throw new FormulaError(`${name}(…) attend ${high === low ? low : high ? `${low} à ${high}` : `au moins ${low}`} valeur(s).`);
      return this.node({ k: "call", name, args });
    }
    if (t.v === "(") {
      const n = this.expr();
      if (!this.op(")")) throw new FormulaError("Parenthèse fermante manquante.");
      return n;
    }
    throw new FormulaError(`« ${t.v} » n'est pas attendu ici.`);
  }
}

const cache = new Map<string, Node>();
function parse(formula: string): Node {
  const hit = cache.get(formula);
  if (hit) return hit;
  if (typeof formula !== "string" || formula.trim() === "") throw new FormulaError("La formule est vide.");
  if (formula.length > MAX_FORMULA) throw new FormulaError(`${MAX_FORMULA} caractères au plus.`);
  const n = new Parser(tokenize(formula)).parse();
  if (cache.size > 500) cache.clear();
  cache.set(formula, n);
  return n;
}

function refsOf(n: Node, out: Set<string>): Set<string> {
  if (n.k === "ref") out.add(n.v);
  else if (n.k === "neg") refsOf(n.a, out);
  else if (n.k === "bin") { refsOf(n.a, out); refsOf(n.b, out); }
  else if (n.k === "call") n.args.forEach((a) => refsOf(a, out));
  return out;
}

/** Keys of the fields a formula reads (nothing for an unreadable formula). */
export function refs(formula: string | undefined): Set<string> {
  if (!formula) return new Set();
  try { return refsOf(parse(formula), new Set()); } catch { return new Set(); }
}

/** "" when the formula is valid, else a message in French. `others`: the keys of the data fields it may read. */
export function check(formula: string, others: Set<string>): string {
  let n: Node;
  try { n = parse(formula); } catch (e) { return e instanceof FormulaError ? e.message : "Formule illisible."; }
  for (const k of [...refsOf(n, new Set())].sort()) if (!others.has(k)) return `« ${k} » doit être un autre champ du formulaire.`;
  return "";
}

// ---- evaluation -----------------------------------------------------------------------------------------------------------------
type V = unknown;
const num = (v: V): number | null => { const n = numOf(v); return n !== null && Number.isFinite(n) && Math.abs(n) < LIMIT ? n : null; };
const emptyV = (v: V) => v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0);
const truthy = (v: V): boolean => (typeof v === "boolean" ? v : v === null || v === undefined ? false : typeof v === "number" ? v !== 0 : textOf(v).length > 0);
const equal = (a: V, b: V): boolean => { const x = numOf(a), y = numOf(b); return x !== null && y !== null ? x === y : textOf(a) === textOf(b); };
const ISO = /^(\d{4})-(\d{2})-(\d{2})$/;
const chars = (s: string) => Array.from(s);

function order(op: string, a: V, b: V): boolean {
  const nx = numOf(a), ny = numOf(b);
  let x: number | string, y: number | string;
  if (nx !== null && ny !== null) { x = nx; y = ny; }
  else if (typeof a === "string" && typeof b === "string" && ISO.test(a) && ISO.test(b)) { x = a; y = b; }
  else return false;
  return op === "<" ? x < y : op === "<=" ? x <= y : op === ">" ? x > y : x >= y;
}

function arith(op: string, a: V, b: V): number | null {
  const x = num(a), y = num(b);
  if (x === null || y === null) return null;
  let r: number;
  if (op === "+") r = x + y;
  else if (op === "-") r = x - y;
  else if (op === "*") r = x * y;
  else if (y === 0) return null;
  else r = op === "/" ? x / y : x % y;
  return Number.isFinite(r) && Math.abs(r) < LIMIT ? r : null;
}

function day(v: V): number | null {
  const m = typeof v === "string" ? ISO.exec(v) : null;
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const t = new Date(Date.UTC(y, mo - 1, d));
  if (y < 1 || t.getUTCFullYear() !== y || t.getUTCMonth() !== mo - 1 || t.getUTCDate() !== d) return null;
  return Math.round(t.getTime() / 86400000);
}

function call(name: string, args: Node[], values: Record<string, V>): V {
  if (name === "if") return ev(truthy(ev(args[0] as Node, values)) ? (args[1] as Node) : (args[2] as Node), values);
  const vals = args.map((a) => ev(a, values));
  const first = vals[0];
  switch (name) {
    case "and": return vals.every(truthy);
    case "or": return vals.some(truthy);
    case "not": return !truthy(first);
    case "min": case "max": case "sum": case "avg": {
      const nums = vals.filter((v) => !emptyV(v)).map(num).filter((n): n is number => n !== null);
      if (name === "sum") return nums.reduce((s, n) => s + n, 0);
      if (nums.length === 0) return null;
      return name === "min" ? Math.min(...nums) : name === "max" ? Math.max(...nums) : nums.reduce((s, n) => s + n, 0) / nums.length;
    }
    case "floor": case "ceil": case "abs": case "round": {
      const x = num(first);
      if (x === null) return null;
      if (name === "abs") return Math.abs(x);
      if (name === "floor") return Math.floor(x);
      if (name === "ceil") return Math.ceil(x);
      const d = vals.length === 1 ? 0 : num(vals[1]);
      if (d === null || !Number.isInteger(d) || d < 0 || d > 10) return null;
      const p = 10 ** d;
      const r = Math.floor(Math.abs(x) * p + 0.5) / p;
      return x < 0 ? -r : r;
    }
    case "len": return chars(textOf(first)).length;
    case "upper": return textOf(first).toUpperCase();
    case "lower": return textOf(first).toLowerCase();
    case "trim": return textOf(first).trim();
    case "concat": return vals.map(textOf).join("");
    case "left": case "right": {
      const n = num(vals[1]);
      if (n === null || n < 0) return null;
      const c = chars(textOf(first)), k = Math.floor(n);
      return name === "left" ? c.slice(0, k).join("") : k ? c.slice(-k).join("") : "";
    }
    case "number": return num(first);
    case "text": return textOf(first);
    case "is_empty": return emptyV(first);
    case "count": return Array.isArray(first) ? first.length : emptyV(first) ? 0 : 1;
    case "sum_col": {
      const key = textOf(vals[1]);
      return (Array.isArray(first) ? first : []).reduce((s: number, r) => { const n = r !== null && typeof r === "object" && !Array.isArray(r) ? num((r as Record<string, unknown>)[key]) : null; return n === null ? s : s + n; }, 0);
    }
    case "coalesce": return vals.find((v) => !emptyV(v)) ?? null;
    default: { const a = day(first), b = day(vals[1]); return a === null || b === null ? null : b - a; }  // days_between
  }
}

function ev(n: Node, values: Record<string, V>): V {
  switch (n.k) {
    case "num": case "str": case "bool": return n.v;
    case "ref": return values[n.v];
    case "neg": { const x = num(ev(n.a, values)); return x === null ? null : -x; }
    case "call": return call(n.name, n.args, values);
    default: {
      const a = ev(n.a, values), b = ev(n.b, values);
      if (n.op === "&") { const s = textOf(a) + textOf(b); return s.length <= MAX_TEXT ? s : null; }
      if (n.op === "=") return equal(a, b);
      if (n.op === "<>") return !equal(a, b);
      if (["<", "<=", ">", ">="].includes(n.op)) return order(n.op, a, b);
      return arith(n.op, a, b);
    }
  }
}

/** The result of a formula, or null when it cannot be computed. */
export function evaluate(formula: string, values: Record<string, unknown>): unknown {
  try {
    const r = ev(parse(formula), values);
    if (typeof r === "number") return Number.isFinite(r) && Math.abs(r) < LIMIT ? r : null;
    if (typeof r === "string" && r.length > MAX_TEXT) return null;
    return r === undefined ? null : r;
  } catch { return null; }
}

/** The result as the value of a field of this type (a number for « number », text otherwise); null if it does not fit. */
export function valueFor(type: FormField["type"], result: unknown): unknown {
  if (result === null || result === undefined) return null;
  if (type === "number") return num(result);
  return textOf(result);
}
