/** Mise en forme Markdown d'une zone de texte : des fonctions pures (texte + sélection → texte + sélection), sans toucher au DOM. */

export type FormatKind = "h1" | "h2" | "h3" | "bold" | "italic" | "strike" | "code" | "link" | "ul" | "ol" | "quote" | "table" | "rule";
export interface Edit {
  value: string;
  start: number;
  end: number;
}

const WRAP: Record<"bold" | "italic" | "strike" | "code", [string, string, string]> = {
  bold: ["**", "**", "texte en gras"],
  italic: ["*", "*", "texte en italique"],
  strike: ["~~", "~~", "texte barré"],
  code: ["`", "`", "code"],
};
const PREFIX: Record<"h1" | "h2" | "h3" | "ul" | "ol" | "quote", string> = { h1: "# ", h2: "## ", h3: "### ", ul: "- ", ol: "1. ", quote: "> " };
const ANY_PREFIX = /^(#{1,6} |[-*+] |\d+[.)] |> )/;

export const TABLE_TEMPLATE = "| Colonne 1 | Colonne 2 |\n|---|---|\n| valeur | valeur |";

function splice(value: string, start: number, end: number, insert: string, selStart: number, selEnd: number): Edit {
  return { value: value.slice(0, start) + insert + value.slice(end), start: selStart, end: selEnd };
}

function wrap(value: string, s: number, e: number, kind: keyof typeof WRAP): Edit {
  const [open, close, placeholder] = WRAP[kind];
  const sel = value.slice(s, e);
  if (sel.startsWith(open) && sel.endsWith(close) && sel.length >= open.length + close.length) {
    const inner = sel.slice(open.length, sel.length - close.length);
    return splice(value, s, e, inner, s, s + inner.length); // déjà mis en forme : on retire
  }
  if (value.slice(s - open.length, s) === open && value.slice(e, e + close.length) === close && sel) {
    return splice(value, s - open.length, e + close.length, sel, s - open.length, e - open.length); // marqueurs autour de la sélection
  }
  const text = sel || placeholder;
  return splice(value, s, e, open + text + close, s + open.length, s + open.length + text.length);
}

function lines(value: string, s: number, e: number, kind: keyof typeof PREFIX): Edit {
  const from = value.lastIndexOf("\n", s - 1) + 1;
  const nl = value.indexOf("\n", e > s && value[e - 1] === "\n" ? e - 1 : e);
  const to = nl === -1 ? value.length : nl;
  const block = value.slice(from, to).split("\n");
  const prefix = PREFIX[kind];
  const marker = (line: string, i: number) => (kind === "ol" ? `${i + 1}. ` : prefix) + line;
  const allHave = block.every((l) => (kind === "ol" ? /^\d+[.)] /.test(l) : l.startsWith(prefix)));
  const out = block.map((l, i) => (allHave ? l.replace(ANY_PREFIX, "") : marker(l.replace(ANY_PREFIX, ""), i)));
  const text = out.join("\n");
  return splice(value, from, to, text, from, from + text.length);
}

function block(value: string, s: number, e: number, insert: string): Edit {
  const before = value.slice(0, s);
  const lead = before === "" || before.endsWith("\n\n") ? "" : before.endsWith("\n") ? "\n" : "\n\n";
  const after = value.slice(e);
  const tail = after === "" || after.startsWith("\n\n") ? "" : after.startsWith("\n") ? "\n" : "\n\n";
  const text = lead + insert + tail;
  return splice(value, s, e, text, s + lead.length, s + lead.length + insert.length);
}

export function applyFormat(value: string, s: number, e: number, kind: FormatKind): Edit {
  if (kind === "bold" || kind === "italic" || kind === "strike" || kind === "code") return wrap(value, s, e, kind);
  if (kind === "link") {
    const label = value.slice(s, e) || "texte du lien";
    const url = "https://";
    const urlStart = s + label.length + 3;
    return splice(value, s, e, `[${label}](${url})`, urlStart, urlStart + url.length);
  }
  if (kind === "table") return block(value, s, e, TABLE_TEMPLATE);
  if (kind === "rule") return block(value, s, e, "---");
  return lines(value, s, e, kind);
}

/** Insère un texte tel quel (image, variable) à la sélection, le curseur après. */
export function insertText(value: string, s: number, e: number, text: string): Edit {
  return splice(value, s, e, text, s + text.length, s + text.length);
}

export const imageMarkdown = (id: string, alt: string): string =>
  `![${
    alt
      .replace(/[[\]\n]/g, " ")
      .replace(/\s+/g, " ")
      .trim() || "image"
  }](image:${id})`;
export const variableMarkdown = (expr: string): string => `{{ ${expr.trim()} }}`;
