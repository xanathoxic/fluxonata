/** The visual CSS designer works on a list of rules. `parse` reads the canonical text the server writes (see
 * triggers/formcss.py) and `serialize` writes the same format back, so what the designer shows, what is stored and what the
 * code editor displays are one text. */
export interface Rule { selectors: string[]; media: string | null; decls: [string, string][] }

const quoteAware = (text: string, from: number, stops: string): number => {
  let quote = "", depth = 0;
  for (let i = from; i < text.length; i++) {
    const c = text[i] as string;
    if (quote) { if (c === quote) quote = ""; continue; }
    if (c === '"' || c === "'") { quote = c; continue; }
    if (c === "(" || c === "[") depth++;
    else if (c === ")" || c === "]") depth--;
    else if (depth <= 0 && stops.includes(c)) return i;
  }
  return text.length;
};

function blockEnd(text: string, from: number): number {
  let depth = 1, quote = "";
  for (let i = from; i < text.length; i++) {
    const c = text[i] as string;
    if (quote) { if (c === quote) quote = ""; continue; }
    if (c === '"' || c === "'") quote = c;
    else if (c === "{") depth++;
    else if (c === "}" && --depth === 0) return i;
  }
  return -1;
}

function readDecls(body: string): [string, string][] | null {
  if (quoteAware(body, 0, "{}") < body.length) return null; // a rule inside a rule is not canonical
  const out: [string, string][] = [];
  let i = 0;
  while (i < body.length) {
    const j = quoteAware(body, i, ";");
    const piece = body.slice(i, j).trim();
    i = j + 1;
    if (!piece) continue;
    const k = piece.indexOf(":");
    if (k < 0) return null;
    out.push([piece.slice(0, k).trim(), piece.slice(k + 1).trim()]);
  }
  return out;
}

function readRules(text: string, media: string | null, depth: number): Rule[] | null {
  const rules: Rule[] = [];
  let i = 0;
  while (i < text.length) {
    while (i < text.length && /\s/.test(text[i] as string)) i++;
    if (i >= text.length) break;
    const open = quoteAware(text, i, "{");
    if (open >= text.length) return null;
    const prelude = text.slice(i, open).trim();
    const close = blockEnd(text, open + 1);
    if (close < 0) return null;
    const inner = text.slice(open + 1, close);
    if (prelude.startsWith("@media")) {
      if (depth > 0) return null;
      const nested = readRules(inner, prelude.slice(6).trim(), depth + 1);
      if (!nested) return null;
      rules.push(...nested);
    } else {
      const decls = readDecls(inner);
      if (!decls) return null;
      const selectors: string[] = [];
      let k = 0;
      while (k <= prelude.length) {
        const e = quoteAware(prelude, k, ",");
        selectors.push(prelude.slice(k, e).trim());
        if (e >= prelude.length) break;
        k = e + 1;
      }
      rules.push({ selectors, media, decls });
    }
    i = close + 1;
  }
  return rules;
}

/** null when the text is not in the canonical form (then only the code editor can handle it). */
export function parse(css: string | undefined): Rule[] | null {
  if (!css || !css.trim()) return [];
  return readRules(css, null, 0);
}

const block = (r: Rule) => `${r.selectors.join(",\n")} {\n${r.decls.map(([p, v]) => `  ${p}: ${v};`).join("\n")}\n}`;

export function serialize(rules: Rule[]): string {
  const out: string[] = [];
  let i = 0;
  const live = rules.filter((r) => r.decls.length > 0);
  while (i < live.length) {
    const r = live[i] as Rule;
    if (r.media === null) { out.push(block(r)); i++; continue; }
    const group: Rule[] = [];
    while (i < live.length && (live[i] as Rule).media === r.media) group.push(live[i++] as Rule);
    out.push(`@media ${r.media} {\n${group.map((g) => block(g).split("\n").map((l) => `  ${l}`).join("\n")).join("\n")}\n}`);
  }
  return out.join("\n\n");
}

export const ruleKey = (r: Pick<Rule, "selectors" | "media">) => `${r.selectors.join("|")}@${r.media ?? ""}`;
export const getDecl = (r: Rule | undefined, prop: string): string => r?.decls.find(([p]) => p === prop)?.[1] ?? "";

/** The rule list with one declaration set (or removed when `value` is empty); the rule is created on its first declaration. */
export function setDecl(rules: Rule[], at: Pick<Rule, "selectors" | "media">, prop: string, value: string): Rule[] {
  const key = ruleKey(at);
  const found = rules.some((r) => ruleKey(r) === key);
  const base = found ? rules : value ? [...rules, { selectors: at.selectors, media: at.media, decls: [] as [string, string][] }] : rules;
  return base
    .map((r) => {
      if (ruleKey(r) !== key) return r;
      const has = r.decls.some(([p]) => p === prop);
      const decls = has ? r.decls.map((d): [string, string] => (d[0] === prop ? [prop, value] : d)) : value ? [...r.decls, [prop, value] as [string, string]] : r.decls;
      return { ...r, decls: decls.filter(([, v]) => v !== "") };
    })
    .filter((r) => r.decls.length > 0);
}

/** Several declarations at once (a shorthand and the longhands it replaces, for instance). */
export function setMany(rules: Rule[], at: Pick<Rule, "selectors" | "media">, changes: Record<string, string>): Rule[] {
  return Object.entries(changes).reduce((acc, [p, v]) => setDecl(acc, at, p, v), rules);
}
