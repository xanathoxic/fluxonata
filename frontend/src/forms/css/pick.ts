/** « Cible libre » sans écrire de chemin : à partir d'un élément de l'aperçu, propose en clair ce qu'on peut viser, et écrit le sélecteur.
 * Tout ce qui est écrit reste dans le vocabulaire que le serveur accepte (triggers/formcss.py) : éléments et classes connus, [data-key], [type], :nth-child. */

/** Éléments HTML que le serveur accepte dans un sélecteur. */
const TAGS = new Set([
  "input",
  "select",
  "textarea",
  "label",
  "button",
  "fieldset",
  "legend",
  "p",
  "h2",
  "h3",
  "h4",
  "hr",
  "div",
  "span",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "small",
  "strong",
  "caption",
  "option",
]);

/** Classes du formulaire et de ses contrôles, avec le nom qu'on leur donne à l'écran (le premier de la liste présent sur l'élément l'emporte). */
const CLASSES: [string, string][] = [
  ["ctl-fold", "bouton replier / déplier"],
  ["ctl-rowhead", "en-tête d'une ligne"],
  ["ctl-rowtools", "outils d'une ligne"],
  ["ctl-details", "détails d'une option"],
  ["ctl-item", "option à cocher"],
  ["ctl-row", "ligne"],
  ["ctl-trow", "ligne de tableau"],
  ["ctl-sum", "résumé d'une ligne"],
  ["ctl-foldbar", "barre replier tout"],
  ["ctl-ihead", "option"],
  ["ctl-body", "contenu d'une ligne"],
  ["fb-req", "astérisque obligatoire"],
  ["field-error", "message d'erreur"],
  ["hint", "aide"],
  ["fb-choice", "option"],
  ["fb-choices", "liste d'options"],
  ["fb-label", "libellé"],
  ["fb-heading", "titre"],
  ["fb-paragraph", "texte"],
  ["fb-divider", "séparateur"],
  ["fb-actions", "zone des boutons"],
  ["fb-section", "section"],
  ["fb-field", "champ (libellé + saisie)"],
  ["fb-cell", "case du champ"],
  ["ctl", "contrôle personnalisé"],
];
const TAG_NAMES: Record<string, string> = {
  label: "libellé",
  input: "zone de saisie",
  select: "liste déroulante",
  textarea: "zone de texte",
  button: "bouton",
  legend: "titre de section",
  p: "texte",
  h2: "titre",
  h3: "titre",
  h4: "titre",
  th: "titre de colonne",
  td: "cellule",
  table: "tableau",
  span: "texte",
  div: "bloc",
  small: "petit texte",
  strong: "texte en gras",
};

export interface Candidate {
  id: string;
  label: string;
  selector: string;
  count: number;
}

const classOf = (el: Element): [string, string] | undefined => CLASSES.find(([c]) => el.classList.contains(c));
/** Le jeton de sélecteur d'un élément, ou "" s'il n'en a pas (il sert alors de simple « niveau » du chemin). */
function token(el: Element): string {
  const tag = el.tagName.toLowerCase();
  const known = classOf(el);
  if (known) return `.${known[0]}`;
  if (tag === "input") {
    const t = el.getAttribute("type");
    return t && /^[a-z]+$/.test(t) ? `input[type="${t}"]` : "input";
  }
  return TAGS.has(tag) ? tag : "";
}

/** L'élément le plus proche de celui qu'on survole qui se laisse viser (jamais le formulaire entier, jamais une icône). */
export function snapTarget(el: Element | null, root: Element): Element | null {
  for (let n = el; n && n !== root; n = n.parentElement) if (root.contains(n) && token(n)) return n;
  return null;
}

/** Le champ (libellé) qui contient l'élément, pour parler en clair. */
const fieldLabel = (el: Element, root: Element): string => {
  const cell = el.closest("[data-key]");
  if (!cell || !root.contains(cell)) return "";
  return (
    (cell.querySelector(":scope > .fb-field > label, :scope > .fb-field > .fb-label")?.textContent ?? "").replace(/\s*\*\s*$/, "").trim() ||
    (cell.getAttribute("data-key") ?? "")
  );
};

/** « Libellé · champ « Ville » » : ce que l'élément est, et dans quel champ. */
export function describeElement(el: Element, root: Element): string {
  const known = classOf(el);
  const name = known ? known[1] : (TAG_NAMES[el.tagName.toLowerCase()] ?? "élément");
  const f = fieldLabel(el, root);
  return `${name[0]?.toUpperCase()}${name.slice(1)}${f ? ` · champ « ${f} »` : ""}`;
}

const matches = (selector: string, root: Element): number => {
  try {
    return root.querySelectorAll(`:scope ${selector}`).length;
  } catch {
    return 0;
  }
};
const nth = (el: Element): string => {
  const parent = el.parentElement;
  if (!parent) return "";
  const t = token(el);
  const same = [...parent.children].filter((c) => token(c) === t);
  return same.length > 1 ? `:nth-child(${[...parent.children].indexOf(el) + 1})` : "";
};

/** Chemin depuis `anchor` (exclu) jusqu'à `el` : enfant direct quand chaque niveau a un jeton, descendant sinon. */
function pathFrom(anchor: Element, el: Element, withNth: boolean): string {
  const chain: Element[] = [];
  for (let n: Element | null = el; n && n !== anchor; n = n.parentElement) chain.unshift(n);
  let out = "";
  let direct = true;
  for (const n of chain) {
    const t = token(n);
    if (!t) {
      direct = false;
      continue;
    }
    out += `${out ? (direct ? " > " : " ") : direct ? "> " : ""}${t}${withNth ? nth(n) : ""}`;
    direct = true;
  }
  return out.trim();
}

/** Ce qu'on peut viser à partir d'un élément : lui seul, ceux du même genre (tous, ou dans ce champ), toute la case du champ. */
export function candidatesFor(el: Element, root: Element): Candidate[] {
  const cell = el.closest("[data-key]");
  const anchor = cell && root.contains(cell) ? cell : root;
  const key = anchor !== root ? anchor.getAttribute("data-key") : null;
  const base = key && /^[A-Za-z0-9 _.-]+$/.test(key) ? `[data-key="${key}"]` : "";
  const tok = token(el);
  const out: Omit<Candidate, "count">[] = [];
  if (anchor === el && base) {
    out.push({ id: "cell", label: "Toute la case de ce champ", selector: base });
  } else {
    const rel = pathFrom(anchor, el, false);
    let precise = `${base} ${rel}`.trim();
    if (matches(precise, root) !== 1) precise = `${base} ${pathFrom(anchor, el, true)}`.trim(); // pas unique : on précise le rang de chaque niveau
    out.push({ id: "one", label: "Cet élément précis", selector: precise });
    const parentTok = el.parentElement && el.parentElement !== root ? token(el.parentElement) : "";
    out.push({ id: "kind", label: "Tous les éléments de ce genre", selector: parentTok ? `${parentTok} > ${tok}` : tok });
    if (base && tok) out.push({ id: "inField", label: "Ceux de ce champ seulement", selector: `${base} ${tok}` });
    if (base) out.push({ id: "cell", label: "Toute la case de ce champ", selector: base });
  }
  const seen = new Set<string>();
  return out
    .filter((c) => c.selector && !seen.has(c.selector) && seen.add(c.selector))
    .map((c) => ({ ...c, count: matches(c.selector, root) }))
    .filter((c) => c.count > 0);
}
