/** Reading and writing the values the controls edit. Everything written stays inside what the server accepts. */
export const UNITS = ["px", "rem", "em", "%", "vw", "vh"] as const;

export function splitLength(v: string): { n: string; unit: string } | null {
  const m = /^(-?(?:\d+\.?\d*|\.\d+))(px|rem|em|%|vw|vh|ch|s|ms|deg)?$/.exec(v.trim());
  return m ? { n: m[1] as string, unit: m[2] ?? "" } : null;
}
export const joinLength = (n: string | number, unit: string) => (n === "" || Number.isNaN(Number(n)) ? "" : `${Number(n)}${unit}`);

export type Box = [string, string, string, string];
/** CSS shorthand of 1 to 4 values, as the four sides (top, right, bottom, left). */
export function parseBox(v: string): Box {
  const p = v.trim().split(/\s+/).filter(Boolean);
  if (p.length === 0) return ["", "", "", ""];
  if (p.length === 1) return [p[0], p[0], p[0], p[0]] as Box;
  if (p.length === 2) return [p[0], p[1], p[0], p[1]] as Box;
  if (p.length === 3) return [p[0], p[1], p[2], p[1]] as Box;
  return [p[0], p[1], p[2], p[3]] as Box;
}
export function formatBox(b: Box): string {
  if (b.every((x) => x === "")) return "";
  const s = b.map((x) => x || "0");
  if (s.every((x) => x === s[0])) return s[0] as string;
  if (s[0] === s[2] && s[1] === s[3]) return `${s[0]} ${s[1]}`;
  return s.join(" ");
}

export interface Rgba { r: number; g: number; b: number; a: number }
export function parseColor(v: string): Rgba | null {
  const s = v.trim();
  let m = /^#([0-9a-fA-F]{6})$/.exec(s);
  if (m) { const n = parseInt(m[1] as string, 16); return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255, a: 1 }; }
  m = /^#([0-9a-fA-F]{3})$/.exec(s);
  if (m) { const [r, g, b] = (m[1] as string).split("").map((c) => parseInt(c + c, 16)); return { r: r as number, g: g as number, b: b as number, a: 1 }; }
  m = /^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*([\d.]+)\s*)?\)$/.exec(s);
  if (m) return { r: Math.min(255, +(m[1] as string)), g: Math.min(255, +(m[2] as string)), b: Math.min(255, +(m[3] as string)), a: m[4] === undefined ? 1 : Math.min(1, +m[4]) };
  return null;
}
const hex2 = (n: number) => Math.round(n).toString(16).padStart(2, "0");
export const toHex = (c: Rgba) => `#${hex2(c.r)}${hex2(c.g)}${hex2(c.b)}`;
export const formatColor = (c: Rgba) => (c.a >= 1 ? toHex(c) : `rgba(${Math.round(c.r)}, ${Math.round(c.g)}, ${Math.round(c.b)}, ${Math.round(c.a * 100) / 100})`);

export interface Shadow { inset: boolean; x: number; y: number; blur: number; spread: number; color: string }
const splitTop = (v: string, sep: string): string[] => {
  const out: string[] = []; let depth = 0, cur = "";
  for (const c of v) { if (c === "(") depth++; if (c === ")") depth--; if (c === sep && depth === 0) { out.push(cur); cur = ""; } else cur += c; }
  if (cur.trim()) out.push(cur);
  return out.map((x) => x.trim());
};
export function parseShadows(v: string): Shadow[] | null {
  if (!v.trim() || v.trim() === "none") return [];
  const out: Shadow[] = [];
  for (const layer of splitTop(v, ",")) {
    const m = /^(inset\s+)?(-?[\d.]+)(?:px)?\s+(-?[\d.]+)(?:px)?(?:\s+(-?[\d.]+)(?:px)?)?(?:\s+(-?[\d.]+)(?:px)?)?\s*(.*)$/.exec(layer);
    if (!m) return null;
    out.push({ inset: Boolean(m[1]), x: +(m[2] as string), y: +(m[3] as string), blur: m[4] ? +m[4] : 0, spread: m[5] ? +m[5] : 0, color: (m[6] || "rgba(0, 0, 0, .2)").trim() });
  }
  return out;
}
export const formatShadows = (l: Shadow[]): string => l.map((s) => `${s.inset ? "inset " : ""}${s.x}px ${s.y}px ${s.blur}px ${s.spread}px ${s.color}`).join(", ");

export interface Gradient { angle: number; from: string; to: string }
export function parseGradient(v: string): Gradient | null {
  const m = /^linear-gradient\(\s*(-?[\d.]+)deg\s*,\s*(.+?)\s*,\s*([^,()]+(?:\([^)]*\))?)\s*\)$/.exec(v.trim());
  return m ? { angle: +(m[1] as string), from: m[2] as string, to: m[3] as string } : null;
}
export const formatGradient = (g: Gradient) => `linear-gradient(${g.angle}deg, ${g.from}, ${g.to})`;

export interface Border { w: string; s: string; c: string }
export function parseBorder(v: string): Border {
  const t = splitTop(v.trim().replace(/\s+/g, " "), " ");
  const out: Border = { w: "", s: "", c: "" };
  for (const x of t) {
    if (/^-?[\d.]+(px|rem|em)?$/.test(x)) out.w = x;
    else if (/^(none|solid|dashed|dotted|double)$/.test(x)) out.s = x;
    else out.c = out.c ? `${out.c} ${x}` : x;
  }
  return out;
}
export const formatBorder = (b: Border) => (!b.w && !b.s && !b.c ? "" : [b.w || "1px", b.s || "solid", b.c || "currentColor"].join(" "));

export const FONT_STACKS: { id: string; label: string; css: string }[] = [
  { id: "system", label: "Système", css: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif' },
  { id: "sans", label: "Sans empattement", css: "Arial, Helvetica, sans-serif" },
  { id: "serif", label: "Avec empattement", css: 'Georgia, "Times New Roman", serif' },
  { id: "rounded", label: "Arrondie", css: 'ui-rounded, "Trebuchet MS", Verdana, sans-serif' },
  { id: "mono", label: "Chasse fixe", css: 'ui-monospace, Menlo, Consolas, monospace' },
];
