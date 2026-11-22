// Échelle de l'interface à la construction : toute longueur en pixels du CSS devient `calc(N px * var(--ui-s))`.
// `--ui-s` vaut 1 par défaut et 0,8 sur ordinateur (≥ 901 px), voir src/theme.css : le rendu est celui d'un navigateur à 80 % de zoom, SANS zoom
// (les calculs de position des menus, info-bulles et éditeurs restent exacts). Retour à l'échelle d'origine : `--ui-s: 1` partout.
// Planchers (accessibilité) : tout `font-size` est borné à 11 px ; les cibles de 24 px minimum sont garanties par src/scale-floors.css.
// Non touchés : les valeurs de 1 px ou moins (filets), les conditions de @media, les url(), les chaînes, la propriété `--ui-s` elle-même.
const FLOOR_FONT = process.env.UI_SCALE_FLOOR_FONT ?? "11px"; // "0px" : mesure de la mise à l'échelle seule, sans plancher (scripts/ui-compare.mjs)
const SKIP_AT = new Set(["font-face", "media", "supports", "container", "charset", "import", "layer"]);
const PX = /(-?\d*\.?\d+)px\b/g;

/** Découpe une valeur en morceaux sûrs (hors chaînes et url()) et applique `fn` aux autres. */
function onSafeParts(value, fn) {
  const parts = value.split(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|url\((?:[^()"']|"[^"]*"|'[^']*')*\))/g);
  return parts.map((p, i) => (i % 2 === 1 ? p : fn(p))).join("");
}

export function scaleLengths(value) {
  return onSafeParts(value, (s) => s.replace(PX, (m, n) => (Math.abs(Number(n)) <= 1 ? m : `calc(${n}px * var(--ui-s))`)));
}

const KEYWORD = /^(inherit|initial|unset|revert|revert-layer|0|xx-small|x-small|small|medium|large|x-large|xx-large|smaller|larger)$/;

/** `font-size: v` → `max(12px, v')` ; sans effet sur 0 et les mots-clés. */
export function floorFontSize(value) {
  const v = value.trim();
  if (KEYWORD.test(v) || /!important$/.test(v) && KEYWORD.test(v.replace(/\s*!important$/, ""))) return value;
  const important = /\s*!important$/.test(v);
  const core = v.replace(/\s*!important$/, "");
  return `max(${FLOOR_FONT}, ${scaleLengths(core)})${important ? " !important" : ""}`;
}

/** Raccourci `font` : style/graisse/variante, puis taille[/hauteur de ligne], puis familles. On borne la taille. */
export function floorFontShorthand(value) {
  const m = value.match(/^(.*?)((?:-?\d*\.?\d+(?:px|rem|em|%|pt|vw|vh))|(?:var\([^)]*\)))((?:\/\s*[^\s,]+)?\s+.+)$/);
  if (!m) return scaleLengths(value);
  const [, before, size, after] = m;
  if (!/(px|rem|em|%|pt|var)/.test(size)) return scaleLengths(value);
  return `${scaleLengths(before)}max(${FLOOR_FONT}, ${scaleLengths(size)})${scaleLengths(after)}`;
}

const plugin = () => ({
  postcssPlugin: "fluxonata-scale",
  Declaration(decl) {
    if (decl.__scaled) return;
    decl.__scaled = true;
    if (/scale-floors\.css$/.test(decl.source?.input?.file ?? "")) return; // planchers et échelles écrits à la main : à ne pas multiplier une seconde fois
    let at = decl.parent;
    while (at && at.type !== "root") { if (at.type === "atrule" && SKIP_AT.has(at.name) && at.name !== "media" && at.name !== "supports" && at.name !== "layer") return; at = at.parent; }
    if (decl.prop === "--ui-s") return;
    if (decl.prop === "font-size") decl.value = floorFontSize(decl.value);
    else if (decl.prop === "font") decl.value = floorFontShorthand(decl.value);
    else if (/px/.test(decl.value)) decl.value = scaleLengths(decl.value);
  },
});
plugin.postcss = true;
export default plugin;
