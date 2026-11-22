import data from "./vocabulary.json";

/** Ce que le serveur accepte dans le CSS d'un formulaire (`triggers/formcss.py`). Le fichier `vocabulary.json` en est la copie :
 * un test du backend vérifie qu'ils restent identiques. Pour le régénérer après un changement du serveur, relire les ensembles
 * LAYOUT, BOX, LOOK, TEXT, ENUMS, FUNCTIONS, UNITS, ELEMENTS, ATTRIBUTES, PSEUDO_CLASSES de `formcss.py`. */
export const VOCAB = data as {
  properties: Record<"layout" | "box" | "look" | "text", string[]>;
  enums: Record<string, string[]>;
  functions: string[];
  units: string[];
  elements: string[];
  attributes: string[];
  pseudoClasses: string[];
};

export const PROPERTY_GROUP: Record<string, string> = { layout: "Disposition", box: "Boîte", look: "Apparence", text: "Texte" };
export const ALL_PROPERTIES: { name: string; group: string }[] = (Object.entries(VOCAB.properties) as [keyof typeof VOCAB.properties, string[]][]).flatMap(
  ([g, list]) => list.map((name) => ({ name, group: PROPERTY_GROUP[g] ?? g })),
);

/** Les repères du formulaire que le CSS peut viser. */
export const CLASSES: [string, string][] = [
  [".fb-section", "une section"],
  [".fb-section > legend", "le titre d'une section"],
  [".fb-grid", "la grille d'une section"],
  [".fb-cell", "la case d'un champ"],
  [".fb-field", "un champ et son libellé"],
  [".fb-field > label", "le libellé d'un champ"],
  [".fb-label", "un libellé (case ou option)"],
  [".fb-req", "l'astérisque « obligatoire »"],
  [".fb-choice", "une option radio ou case"],
  [".fb-heading", "un bloc titre"],
  [".fb-paragraph", "un bloc texte"],
  [".fb-divider", "un séparateur"],
  [".fb-actions", "la zone du bouton d'envoi"],
  [".fb-actions button", "les boutons"],
  [".hint", "l'aide sous un champ"],
  [".field-error", "le message d'erreur d'un champ"],
  [".ctl", "un contrôle personnalisé"],
];
export const VARIABLES: [string, string][] = [
  ["--accent", "couleur d'accent"],
  ["--accent-fg", "texte sur l'accent"],
  ["--fg", "couleur du texte"],
  ["--muted", "texte discret"],
  ["--border", "couleur des bordures"],
  ["--surface", "fond des cartes"],
  ["--fb-gap", "espace entre les champs"],
  ["--fb-pad", "espacement intérieur"],
  ["--fb-radius", "arrondi des coins"],
];
export const SNIPPETS: { label: string; css: string }[] = [
  { label: "Titres en couleur", css: ".fb-heading {\n  color: #1d4ed8;\n  letter-spacing: .02em;\n}" },
  { label: "Libellés en gras", css: "label, .fb-label {\n  font-weight: 700;\n}" },
  { label: "Champs à fond clair", css: "input, select, textarea {\n  background: #f8fafc;\n  border: 1px solid #cbd5e1;\n  border-radius: 8px;\n}" },
  {
    label: "Anneau de focus",
    css: "input:focus-visible, select:focus-visible, textarea:focus-visible {\n  outline: 2px solid var(--accent);\n  box-shadow: 0 0 0 3px rgba(29, 78, 216, .25);\n}",
  },
  {
    label: "Bouton d'envoi plein",
    css: ".fb-actions button {\n  background: var(--accent);\n  color: var(--accent-fg);\n  border-radius: 999px;\n  padding: 10px 22px;\n  font-weight: 700;\n}",
  },
  {
    label: "Sections en cartes",
    css: ".fb-section {\n  border: 1px solid var(--border);\n  border-radius: 14px;\n  padding: 18px;\n  box-shadow: 0 2px 8px rgba(15, 23, 42, .08);\n}",
  },
  { label: "Un champ précis", css: '[data-key="nom"] input {\n  border-color: #1d4ed8;\n}' },
  { label: "Petits écrans", css: "@media (max-width: 600px) {\n  .fb-section {\n    padding: 8px;\n  }\n}" },
  { label: "Mode sombre", css: "@media (prefers-color-scheme: dark) {\n  .fb-paragraph {\n    color: #cbd5e1;\n  }\n}" },
];
