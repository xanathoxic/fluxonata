/** Calculs de couleur de l'image de marque. Mêmes formules que backend/tenancy/branding.py (le serveur refuse une couleur trop claire) :
 *  palette.test.ts et tests/test_branding.py partagent des valeurs de référence pour que les deux restent identiques. */

export const DEFAULT_ACCENT = "#0e7490";
export const MIN_CONTRAST = 4.5;
export const WHITE = "#ffffff";
export const PAGE_BG = "#f6f8fb";
const SOFT_WEIGHT = 0.12;
const HEX = /^#[0-9a-fA-F]{6}$/;

export const isHex = (v: string): boolean => HEX.test(v.trim());

export function rgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
const toHex = (r: number, g: number, b: number): string =>
  `#${[r, g, b]
    .map((c) =>
      Math.max(0, Math.min(255, Math.round(c)))
        .toString(16)
        .padStart(2, "0"),
    )
    .join("")}`;
const lin = (c: number): number => {
  const v = c / 255;
  return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
};
export function luminance(hex: string): number {
  const [r, g, b] = rgb(hex);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
export function contrast(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}
/** Mélange `a` avec `b` : `weightB` = part de `b` (0 à 1). */
export function mix(a: string, b: string, weightB: number): string {
  const [ra, ga, ba] = rgb(a);
  const [rb, gb, bb] = rgb(b);
  return toHex(ra + (rb - ra) * weightB, ga + (gb - ga) * weightB, ba + (bb - ba) * weightB);
}
export const soft = (accent: string): string => mix(WHITE, accent, SOFT_WEIGHT);

export interface Report {
  onWhite: number;
  onPage: number;
  onSoft: number;
  buttonText: number;
  worst: number;
  ok: boolean;
}
/** Contrastes de l'accent : en texte sur les trois fonds de l'interface, et sous un texte blanc (boutons). */
export function report(accent: string): Report {
  const onWhite = contrast(accent, WHITE);
  const onPage = contrast(accent, PAGE_BG);
  const onSoft = contrast(accent, soft(accent));
  const worst = Math.min(onWhite, onPage, onSoft);
  return { onWhite, onPage, onSoft, buttonText: onWhite, worst, ok: worst >= MIN_CONTRAST };
}

/** La teinte la plus proche de `accent` (même couleur, plus sombre) qui respecte le seuil AA. */
export function nearestAccessible(accent: string): string {
  if (!isHex(accent)) return DEFAULT_ACCENT;
  let c = accent.toLowerCase();
  for (let i = 0; i < 40 && !report(c).ok; i++) c = mix(c, "#000000", 0.04);
  return c;
}

/** Variables CSS posées sur `:root` pour une couleur d'accent personnalisée (vide si l'accent est celui d'origine). */
export function themeVars(accent: string, force = false): Record<string, string> {
  if (!isHex(accent) || (!force && accent.toLowerCase() === DEFAULT_ACCENT)) return {};
  const a = accent.toLowerCase();
  return { "--accent": a, "--primary": a, "--link": a, "--focus": a, "--accent-soft": soft(a), "--accent-fg": WHITE };
}

/** Palettes proposées : toutes respectent le seuil AA (vérifié par palette.test.ts). */
export const PRESETS: { name: string; accent: string }[] = [
  { name: "Océan (d'origine)", accent: DEFAULT_ACCENT },
  { name: "Azur", accent: "#0b5ed7" },
  { name: "Indigo", accent: "#4338ca" },
  { name: "Violet", accent: "#6d28d9" },
  { name: "Prune", accent: "#a21caf" },
  { name: "Grenat", accent: "#b91c1c" },
  { name: "Cuivre", accent: "#9a3412" },
  { name: "Forêt", accent: "#166534" },
  { name: "Ardoise", accent: "#334155" },
];

// ───────── Barres (menu de gauche, barre du haut) ─────────

export interface NavTheme {
  bg: string;
  fg: string;
  /** Texte secondaire (titres de groupes, identifiant) : atténué, mais toujours lisible. */
  muted: string;
  border: string;
  /** Fond d'un élément survolé, d'un champ (espace, environnement), d'un bouton. */
  hover: string;
  /** Fond de l'élément de menu actif. */
  active: string;
  dark: boolean;
}

/** Texte posé sur un fond : blanc sur un fond sombre, sinon l'encre de l'interface, sinon noir (le gris moyen n'atteint 4,5:1 qu'avec du noir). */
export function readableOn(bg: string): string {
  const candidates = luminance(bg) < 0.18 ? [WHITE] : ["#0f172a", "#000000"];
  for (const c of candidates) if (contrast(c, bg) >= MIN_CONTRAST) return c;
  return contrast(WHITE, bg) >= contrast("#000000", bg) ? WHITE : "#000000";
}

/** Tout ce qui compose une barre se déduit de son fond, de sorte que le texte, les icônes, le survol et l'élément actif atteignent 4,5:1
 *  quel que soit le fond choisi (testé sur une grille de couleurs dans palette.test.ts). */
export function navTheme(bgInput: string): NavTheme {
  const bg = isHex(bgInput) ? bgInput.toLowerCase() : WHITE;
  const fg = readableOn(bg);
  const tinted = (weight: number, floor: string): string => {
    for (let w = weight; w > 0; w -= 0.01) {
      const c = mix(bg, fg, w);
      if (contrast(fg, c) >= MIN_CONTRAST && contrast(fg, floor) >= MIN_CONTRAST) return c;
    }
    return bg;
  };
  const hover = tinted(0.09, bg);
  const active = tinted(0.18, hover);
  let muted = fg;
  for (let w = 0.32; w >= 0; w -= 0.02) {
    const c = mix(fg, bg, w);
    if (contrast(c, bg) >= MIN_CONTRAST && contrast(c, hover) >= MIN_CONTRAST && contrast(c, active) >= MIN_CONTRAST) {
      muted = c;
      break;
    }
  }
  return { bg, fg, muted, border: mix(bg, fg, 0.16), hover, active, dark: fg === WHITE };
}

/** Variables CSS d'une barre (`prefix` : « tb » pour la barre du haut, « sb » pour le menu de gauche). */
export function navVars(prefix: "tb" | "sb", bg: string): Record<string, string> {
  const t = navTheme(bg);
  const stroke = t.fg.replace("#", "%23");
  const chevron = `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='${stroke}' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E")`;
  return {
    [`--${prefix}-bg`]: t.bg,
    [`--${prefix}-fg`]: t.fg,
    [`--${prefix}-muted`]: t.muted,
    [`--${prefix}-border`]: t.border,
    [`--${prefix}-hover`]: t.hover,
    [`--${prefix}-active`]: t.active,
    [`--${prefix}-chevron`]: chevron,
  };
}

/** Fonds proposés pour les barres (le blanc d'origine n'en fait pas partie : « Couleur d'origine » le rétablit). */
export const NAV_PRESETS: { name: string; bg: string }[] = [
  { name: "Gris clair", bg: "#f1f5f9" },
  { name: "Ardoise", bg: "#334155" },
  { name: "Nuit", bg: "#0f172a" },
  { name: "Marine", bg: "#0b1f4b" },
  { name: "Forêt", bg: "#14532d" },
  { name: "Prune", bg: "#4a044e" },
];
