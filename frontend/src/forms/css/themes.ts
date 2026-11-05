import { setDecl, type Rule } from "./model";
import { ruleOf } from "./targets";

/** Ready-made looks: sets of rules to apply, then to adapt with the designer. */
export interface Theme { id: string; label: string; description: string; swatch: [string, string]; rules: Rule[] }

const r = (part: string, decls: Record<string, string>, o: { state?: string; screen?: string } = {}): Rule => ({
  ...ruleOf({ scope: { kind: "all" }, part, state: o.state ?? "normal", screen: o.screen ?? "all" }),
  decls: Object.entries(decls),
});
const FOCUS = "0 0 0 3px color-mix(in srgb, var(--accent) 28%, transparent)";

export const THEMES: Theme[] = [
  { id: "epure", label: "Épuré", description: "Champs soulignés, libellés discrets, aucun cadre.", swatch: ["#ffffff", "#94a3b8"], rules: [
    r("section", { border: "0", padding: "0", background: "transparent" }),
    r("label", { "font-size": "0.82rem", "text-transform": "uppercase", "letter-spacing": "0.05em", color: "var(--muted)" }),
    r("input", { border: "0", "border-bottom": "2px solid var(--border)", "border-radius": "0", background: "transparent", padding: "6px 2px" }),
    r("input", { "border-bottom-color": "var(--accent)", "box-shadow": "none" }, { state: "focus" }),
    r("button", { "border-radius": "0", "text-transform": "uppercase", "letter-spacing": "0.06em" }),
  ] },
  { id: "doux", label: "Doux", description: "Coins très arrondis, fonds clairs, ombres légères.", swatch: ["#f8fafc", "#1d4ed8"], rules: [
    r("section", { "border-radius": "20px", "box-shadow": "0 8px 24px rgba(15, 23, 42, 0.08)", border: "0", padding: "24px" }),
    r("input", { "border-radius": "14px", background: "#f8fafc", border: "1px solid #e2e8f0", padding: "10px 14px" }),
    r("input", { "box-shadow": FOCUS, "border-color": "var(--accent)" }, { state: "focus" }),
    r("button", { "border-radius": "999px", padding: "10px 24px", "font-weight": "600" }),
    r("heading", { "font-weight": "700", "letter-spacing": "-0.01em" }),
  ] },
  { id: "contraste", label: "Contraste élevé", description: "Bordures épaisses, focus très visible, boutons pleins.", swatch: ["#0f172a", "#ffffff"], rules: [
    r("label", { "font-weight": "700", color: "#0f172a" }),
    r("input", { border: "2px solid #0f172a", "border-radius": "4px", color: "#0f172a" }),
    r("input", { outline: "3px solid #b45309", "outline-offset": "2px" }, { state: "focus-visible" }),
    r("button", { background: "#0f172a", color: "#ffffff", border: "2px solid #0f172a", "border-radius": "4px", "font-weight": "700" }),
    r("button", { background: "#ffffff", color: "#0f172a" }, { state: "hover" }),
    r("help", { color: "#334155" }),
  ] },
  { id: "corporate", label: "Corporate", description: "Titres en couleur d'accent, filet latéral, sobre.", swatch: ["#e0f2f7", "#0e7490"], rules: [
    r("section", { "border-left": "4px solid var(--accent)", "border-radius": "6px" }),
    r("sectionTitle", { color: "var(--accent)", "font-weight": "700", "text-transform": "uppercase", "letter-spacing": "0.06em", "font-size": "0.8rem" }),
    r("heading", { color: "var(--accent)" }),
    r("label", { "font-weight": "600" }),
    r("input", { border: "1px solid #cbd5e1", "border-radius": "6px" }),
    r("button", { "border-radius": "6px", "font-weight": "600" }),
  ] },
  { id: "moderne", label: "Moderne", description: "Bouton en dégradé qui se soulève, champs arrondis.", swatch: ["#1d4ed8", "#6d28d9"], rules: [
    r("input", { "border-radius": "12px", border: "1px solid #cbd5e1", padding: "10px 12px", transition: "all 150ms ease" }),
    r("input", { "box-shadow": FOCUS, "border-color": "var(--accent)" }, { state: "focus" }),
    r("button", { "background-image": "linear-gradient(135deg, var(--accent), #6d28d9)", color: "#ffffff", border: "0", "border-radius": "12px", padding: "11px 26px", "font-weight": "600", transition: "all 150ms ease" }),
    r("button", { transform: "translateY(-1px)", "box-shadow": "0 8px 20px rgba(29, 78, 216, 0.3)" }, { state: "hover" }),
  ] },
  { id: "sombre", label: "Sombre", description: "Fond sombre, champs ardoise, texte clair.", swatch: ["#0f172a", "#e2e8f0"], rules: [
    r("root", { background: "#0f172a", color: "#e2e8f0", padding: "16px", "border-radius": "14px" }),
    r("section", { background: "#111c33", border: "1px solid #334155" }),
    r("label", { color: "#cbd5e1" }),
    r("input", { background: "#1e293b", border: "1px solid #334155", color: "#f1f5f9" }),
    r("help", { color: "#94a3b8" }),
    r("heading", { color: "#f8fafc" }),
    r("paragraph", { color: "#cbd5e1" }),
  ] },
];

export function applyTheme(rules: Rule[], theme: Theme, mode: "replace" | "merge"): Rule[] {
  if (mode === "replace") return theme.rules.map((x) => ({ ...x, decls: [...x.decls] as [string, string][] }));
  return theme.rules.reduce((acc, rule) => rule.decls.reduce((a, [p, v]) => setDecl(a, rule, p, v), acc), rules);
}
