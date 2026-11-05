import type { Rule } from "./model";

/** What can be styled, in which state and on which screen. A rule of the designer is one (scope, part, state, screen); the
 * selectors written for it are the closed vocabulary the server accepts (triggers/formcss.py). */
export type Scope = { kind: "all" } | { kind: "key"; key: string } | { kind: "type"; type: string };
export interface Part { id: string; label: string; icon: string; rel: string[]; scoped: boolean; /** the scoped selector is the cell itself */ cell?: boolean; group: "form" | "field" | "content" | "action" }

export const PARTS: Part[] = [
  { id: "root", label: "Tout le formulaire", icon: "design", rel: [""], scoped: false, group: "form" },
  { id: "section", label: "Sections", icon: "section", rel: [".fb-section"], scoped: false, group: "form" },
  { id: "sectionTitle", label: "Titres de section", icon: "heading", rel: [".fb-section > legend"], scoped: false, group: "form" },
  { id: "cell", label: "Cases des champs", icon: "width3", rel: [".fb-cell"], scoped: true, cell: true, group: "field" },
  { id: "field", label: "Champs (libellé + saisie)", icon: "text", rel: [".fb-field"], scoped: true, group: "field" },
  { id: "label", label: "Libellés", icon: "paragraph", rel: [".fb-field > label", ".fb-field > .fb-label"], scoped: true, group: "field" },
  { id: "input", label: "Zones de saisie", icon: "textarea", rel: ['input[type="text"]', 'input[type="email"]', 'input[type="number"]', 'input[type="date"]', "select", "textarea"], scoped: true, group: "field" },
  { id: "choice", label: "Options (radio, cases)", icon: "radio", rel: [".fb-choice"], scoped: true, group: "field" },
  { id: "help", label: "Aides", icon: "info", rel: [".hint"], scoped: true, group: "field" },
  { id: "error", label: "Messages d'erreur", icon: "warn", rel: [".field-error"], scoped: true, group: "field" },
  { id: "required", label: "Astérisque obligatoire", icon: "number", rel: [".fb-req"], scoped: true, group: "field" },
  { id: "heading", label: "Blocs titre", icon: "heading", rel: [".fb-heading"], scoped: false, group: "content" },
  { id: "paragraph", label: "Blocs texte", icon: "paragraph", rel: [".fb-paragraph"], scoped: false, group: "content" },
  { id: "divider", label: "Séparateurs", icon: "divider", rel: [".fb-divider"], scoped: false, group: "content" },
  { id: "table", label: "Tableaux", icon: "datagrid", rel: ["table", "th", "td"], scoped: false, group: "content" },
  { id: "actions", label: "Zone des boutons", icon: "btnFull", rel: [".fb-actions"], scoped: false, group: "action" },
  { id: "button", label: "Boutons", icon: "play", rel: [".fb-actions button"], scoped: false, group: "action" },
];
export const CUSTOM = "custom";

export interface State { id: string; label: string; suffix: string; icon: string }
export const STATES: State[] = [
  { id: "normal", label: "Normal", suffix: "", icon: "design" },
  { id: "hover", label: "Survol", suffix: ":hover", icon: "play" },
  { id: "focus", label: "Focus clavier ou souris", suffix: ":focus", icon: "search" },
  { id: "focus-visible", label: "Focus clavier", suffix: ":focus-visible", icon: "search" },
  { id: "active", label: "Clic en cours", suffix: ":active", icon: "down" },
  { id: "disabled", label: "Désactivé", suffix: ":disabled", icon: "pause" },
  { id: "checked", label: "Coché", suffix: ":checked", icon: "check" },
  { id: "placeholder", label: "Texte d'exemple", suffix: "::placeholder", icon: "textarea" },
  { id: "invalid", label: "En erreur", suffix: '[aria-invalid="true"]', icon: "warn" },
];

export interface Screen { id: string; label: string; media: string | null; icon: string }
export const SCREENS: Screen[] = [
  { id: "all", label: "Tous les écrans", media: null, icon: "width3" },
  { id: "mobile", label: "Mobile (jusqu'à 600 px)", media: "(max-width: 600px)", icon: "width1" },
  { id: "tablet", label: "Tablette (jusqu'à 900 px)", media: "(max-width: 900px)", icon: "width2" },
  { id: "desktop", label: "Grand écran (dès 900 px)", media: "(min-width: 900px)", icon: "width4" },
  { id: "dark", label: "Mode sombre", media: "(prefers-color-scheme: dark)", icon: "dense3" },
  { id: "motion", label: "Mouvement réduit", media: "(prefers-reduced-motion: reduce)", icon: "pause" },
];

export interface Target { scope: Scope; part: string; state: string; screen: string; custom?: string }
export const defaultTarget = (): Target => ({ scope: { kind: "all" }, part: "label", state: "normal", screen: "all" });

const scopePrefix = (s: Scope): string => (s.kind === "key" ? `[data-key="${s.key}"]` : s.kind === "type" ? `[data-type="${s.type}"]` : "");
const join = (...p: string[]) => p.filter(Boolean).join(" ");

export function selectorsFor(t: Target): string[] {
  if (t.part === CUSTOM) return (t.custom ?? "").split(",").map((x) => x.trim()).filter(Boolean).map((x) => (x.startsWith("&") ? x : `& ${x}`));
  const part = PARTS.find((p) => p.id === t.part) ?? (PARTS[0] as Part);
  const state = STATES.find((s) => s.id === t.state)?.suffix ?? "";
  const scope = part.scoped ? scopePrefix(t.scope) : "";
  return part.rel.map((rel) => {
    const base = part.cell && scope ? scope : join(scope, rel);
    return base === "" ? `&${state}` : `& ${base}${state}`;
  });
}
export const mediaFor = (t: Target): string | null => SCREENS.find((s) => s.id === t.screen)?.media ?? null;
export const ruleOf = (t: Target): Pick<Rule, "selectors" | "media"> => ({ selectors: selectorsFor(t), media: mediaFor(t) });

const SCOPE_RE = /^& \[data-(key|type)="([^"]+)"\]/;

/** The target a rule's selectors stand for, or a custom one when they do not match anything the designer writes itself. */
export function targetOf(r: Pick<Rule, "selectors" | "media">): Target {
  const screen = SCREENS.find((s) => s.media === r.media)?.id ?? "all";
  const m = SCOPE_RE.exec(r.selectors[0] ?? "");
  const scopes: Scope[] = m ? [m[1] === "key" ? { kind: "key", key: m[2] as string } : { kind: "type", type: m[2] as string }] : [{ kind: "all" }];
  for (const scope of scopes) for (const part of PARTS) for (const state of STATES) {
    if (scope.kind !== "all" && !part.scoped) continue;
    const t: Target = { scope, part: part.id, state: state.id, screen };
    if (selectorsFor(t).join("|") === r.selectors.join("|")) return t;
  }
  return { scope: { kind: "all" }, part: CUSTOM, state: "normal", screen: r.media && screen === "all" ? "all" : screen, custom: r.selectors.map((s) => s.replace(/^& ?/, "")).join(", ") };
}

export function describe(r: Pick<Rule, "selectors" | "media">): string {
  const t = targetOf(r);
  const part = t.part === CUSTOM ? `« ${t.custom} »` : (PARTS.find((p) => p.id === t.part)?.label ?? t.part);
  const scope = t.scope.kind === "key" ? ` · champ ${t.scope.key}` : t.scope.kind === "type" ? ` · type ${t.scope.type}` : "";
  const state = t.state !== "normal" ? ` · ${STATES.find((s) => s.id === t.state)?.label.toLowerCase()}` : "";
  const screen = r.media ? ` · ${SCREENS.find((s) => s.media === r.media)?.label ?? r.media}` : "";
  return `${part}${scope}${state}${screen}`;
}

/** What the person clicked in the preview, as a target (the scope offered is the field it sits in). */
export function targetFromElement(el: Element, root: Element): { part: string; key?: string; type?: string } | null {
  const cell = el.closest("[data-key]");
  const info = cell && root.contains(cell) ? { key: cell.getAttribute("data-key") ?? undefined, type: cell.getAttribute("data-type") ?? undefined } : {};
  const checks: [string, string][] = [
    [".fb-req", "required"], [".field-error", "error"], [".hint", "help"], [".fb-actions button", "button"], [".fb-actions", "actions"],
    [".fb-choice", "choice"], ["input, select, textarea", "input"], [".fb-field > label, .fb-field > .fb-label", "label"],
    [".fb-heading", "heading"], [".fb-paragraph", "paragraph"], [".fb-divider", "divider"], ["table, th, td", "table"],
    [".fb-section > legend", "sectionTitle"], [".fb-field", "field"], [".fb-cell", "cell"], [".fb-section", "section"],
  ];
  for (const [sel, part] of checks) { const hit = el.closest(sel); if (hit && root.contains(hit)) return { part, ...info }; }
  return root === el || root.contains(el) ? { part: "root", ...info } : null;
}
