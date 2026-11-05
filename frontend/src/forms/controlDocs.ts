import { detailsOf, hasDetails, MAX_ITEMS, MAX_ROWS, type ControlItem, type ControlSpec } from "./controls";
import { FIELD_TYPES, isBlock, type FormField } from "./types";

/** What a control hands to the workflow and accepts from a form, written out from its description (the documentation page is generated
 *  from this, so it follows the design as it is made). The schema is plain JSON Schema (draft 2020-12 keywords only). */
export type Schema = Record<string, unknown>;
const clean = (s: Schema): Schema => Object.fromEntries(Object.entries(s).filter(([, v]) => v !== undefined && !(Array.isArray(v) && v.length === 0)));
const values = (p: FormField): string[] | undefined => (!p.source && p.options?.length ? p.options.map((o) => o.value) : undefined);

export function fieldSchema(p: FormField): Schema {
  const base: Schema = { title: p.label || p.key, ...(p.compute ? { readOnly: true } : {}) };
  switch (p.type) {
    case "number": return clean({ ...base, type: "number", minimum: p.min, maximum: p.max });
    case "email": return clean({ ...base, type: "string", format: "email" });
    case "date": return clean({ ...base, type: "string", format: "date" });
    case "checkbox": return clean({ ...base, type: "boolean" });
    case "select": case "radio": case "combobox": return clean({ ...base, type: "string", enum: values(p) });
    case "checkbox_list": return clean({ ...base, type: "array", items: clean({ type: "string", enum: values(p) }), uniqueItems: true });
    case "datagrid": return clean({ ...base, type: "array", items: { type: "object", properties: Object.fromEntries((p.columns ?? []).map((c) => [c.key, { type: c.type === "number" ? "number" : "string" }])) } });
    case "custom": return p.control?.spec ? { ...base, ...valueSchema(p.control.spec) } : { ...base, type: "object" };
    default: return clean({ ...base, type: "string" });
  }
}

const dataParts = (parts: FormField[]): FormField[] => parts.filter((p) => !isBlock(p.type));
const objectOf = (parts: FormField[], extra: Record<string, Schema> = {}, required: string[] = []): Schema => {
  const data = dataParts(parts);
  return clean({ type: "object", properties: { ...extra, ...Object.fromEntries(data.map((p) => [p.key, fieldSchema(p)])) }, required: [...required, ...data.filter((p) => p.required && !p.compute).map((p) => p.key)], additionalProperties: false });
};
const allDetails = (spec: ControlSpec): FormField[] => [...(spec.parts ?? []), ...(spec.items ?? []).flatMap((o) => o.parts ?? [])];

/** The value of the control, as the workflow receives it (`inputs.<clé du champ>`). */
export function valueSchema(spec: ControlSpec): Schema {
  const parts = spec.parts ?? [];
  if (spec.mode === "group") return objectOf(parts);
  if (spec.mode === "repeat") return clean({ type: "array", items: objectOf(parts), minItems: spec.min, maxItems: spec.max ?? MAX_ROWS });
  const fixed = spec.dynamic ? undefined : (spec.items ?? []).map((o) => o.value);
  const choice: Schema = clean({ type: "string", enum: fixed });
  const entry: Schema = hasDetails(spec) ? objectOf(allDetails(spec), { value: choice }, ["value"]) : choice;
  if (spec.mode === "choice") return entry;
  return clean({ type: "array", items: entry, uniqueItems: true, minItems: spec.min, maxItems: spec.max ?? MAX_ITEMS });
}

/** One option, as a service may send it (the keys of the fields it opens pre-fill them when it is chosen). */
export function optionSchema(spec: ControlSpec): Schema {
  const data = dataParts(spec.parts ?? []).filter((p) => !p.compute);
  return {
    type: "object",
    properties: {
      value: { type: "string", maxLength: 200, description: "Valeur remise au workflow (obligatoire, unique)." },
      label: { type: "string", maxLength: 200, description: "Texte affiché (la valeur par défaut)." },
      group: { type: "string", maxLength: 120, description: "Titre du groupe sous lequel l'option est rangée." },
      disabled: { type: "boolean", description: "Visible mais non sélectionnable." },
      ...Object.fromEntries(data.map((p) => [p.key, { ...fieldSchema(p), description: "Pré-remplit ce champ quand l'option est choisie." }])),
    },
    required: ["value"],
  };
}

export interface DocField { key: string; label: string; typeLabel: string; json: string; required: boolean; notes: string[] }
const JSON_OF: Record<string, string> = { number: "nombre", checkbox: "booléen", checkbox_list: "liste de textes", datagrid: "liste d'objets", custom: "objet ou liste (contrôle imbriqué)" };
/** The sub-fields, one line each. */
export function fieldRows(parts: FormField[]): DocField[] {
  return dataParts(parts).map((p) => {
    const notes: string[] = [];
    if (p.compute) notes.push(`calculé : ${p.compute}`);
    if (p.min !== undefined) notes.push(`minimum ${p.min}`);
    if (p.max !== undefined) notes.push(`maximum ${p.max}`);
    if (p.options?.length && !p.source) notes.push(`choix : ${p.options.slice(0, 6).map((o) => o.value).join(", ")}${p.options.length > 6 ? "…" : ""}`);
    if (p.rules?.length) notes.push(`${p.rules.length} règle(s)`);
    if (p.visible_if) notes.push("affiché sous condition");
    if (p.type === "custom" && p.control) notes.push(`contrôle « ${p.control.name ?? p.control.ref} »`);
    return { key: p.key, label: p.label || p.key, typeLabel: FIELD_TYPES.find((f) => f.type === p.type)?.label ?? p.type, json: JSON_OF[p.type] ?? "texte", required: Boolean(p.required) && !p.compute, notes };
  });
}

export interface Access { goal: string; where: string; syntax: string }
/** How the rest of the form and the workflow reach this control and what is inside it. `<clé>` stands for the key the form gives to the field. */
export function interactions(spec: ControlSpec): Access[] {
  const first = dataParts(spec.parts ?? [])[0]?.key ?? "champ";
  const num = dataParts(spec.parts ?? []).find((p) => p.type === "number")?.key ?? "total";
  const out: Access[] = [];
  const wf = (goal: string, syntax: string) => out.push({ goal, where: "Workflow (entrées)", syntax });
  const inside = (goal: string, syntax: string) => out.push({ goal, where: "Dans le contrôle (conditions, règles, formules)", syntax });
  const form = (goal: string, syntax: string) => out.push({ goal, where: "Formulaire (formules)", syntax });
  const details = hasDetails(spec);
  switch (spec.mode) {
    case "group":
      wf("Tout l'objet", "{{ inputs.<clé> }}");
      wf("Un sous-champ", `{{ inputs.<clé>.${first} }}`);
      inside("Lire un autre sous-champ", `{${first}}`);
      break;
    case "repeat":
      wf("Toute la liste de lignes", "{{ inputs.<clé> }}");
      wf("Un champ de la première ligne", `{{ inputs.<clé>[0].${first} }}`);
      form("Nombre de lignes", "count({<clé>})");
      form("Total d'une colonne numérique", `sum_col({<clé>}, "${num}")`);
      inside("Lire un autre champ de la même ligne", `{${first}}`);
      break;
    case "choice":
      wf(details ? "La valeur choisie" : "Le choix", details ? "{{ inputs.<clé>.value }}" : "{{ inputs.<clé> }}");
      if (details) wf("Un champ de détail de l'option", `{{ inputs.<clé>.${first} }}`);
      if (details) inside("Lire un autre champ de détail", `{${first}}`);
      break;
    default:
      wf("Toute la sélection", "{{ inputs.<clé> }}");
      wf(details ? "La valeur du premier choix" : "Le premier choix", details ? "{{ inputs.<clé>[0].value }}" : "{{ inputs.<clé>[0] }}");
      if (details) wf("Un champ de détail du premier choix", `{{ inputs.<clé>[0].${first} }}`);
      form("Nombre de choix cochés", "count({<clé>})");
      if (details) inside("Lire un autre champ de détail", `{${first}}`);
  }
  out.push({ goal: "Pré-remplir la valeur depuis un service", where: "Champ du formulaire → Données depuis un service → Valeur préremplie", syntax: "la réponse doit respecter le schéma de la valeur (refusée en entier sinon)" });
  if ((spec.mode === "items" || spec.mode === "choice") && spec.dynamic) {
    out.push({ goal: "Fournir les options depuis un service", where: "Champ du formulaire → Données depuis un service → Options", syntax: "liste d'objets décrits dans « Options reçues »" });
  }
  out.push({ goal: "Charger les données au bon moment", where: "Champ du formulaire → Conditions et règles", syntax: "effet « Charger les options / la valeur préremplie »" });
  out.push({ goal: "Passer une valeur d'un autre champ au service", where: "Données depuis un service → Paramètres", syntax: "{nom} dans le chemin, rempli par un champ du formulaire" });
  return out;
}

// ---- examples (also used by the Test tab)

function cell(p: FormField): unknown {
  switch (p.type) {
    case "number": return p.min ?? 1;
    case "email": return "nom@exemple.fr";
    case "date": return "2026-01-15";
    case "checkbox": return true;
    case "select": case "radio": case "combobox": return p.options?.[0]?.value;
    case "checkbox_list": return p.options?.[0] ? [p.options[0].value] : [];
    default: return p.type === "custom" ? undefined : "Exemple";
  }
}
export const exampleRow = (parts: FormField[]): Record<string, unknown> =>
  Object.fromEntries(parts.filter((p) => !isBlock(p.type) && !p.compute).map((p) => [p.key, cell(p)]).filter(([, v]) => v !== undefined));

/** A value that respects the control's description, to start from (the options are those sent, or the control's own). */
export function exampleValue(spec: ControlSpec, options: ControlItem[]): unknown {
  const parts = spec.parts ?? [];
  if (spec.mode === "group") return exampleRow(parts);
  if (spec.mode === "repeat") return [exampleRow(parts), exampleRow(parts)];
  const pick = options.filter((o) => !o.disabled).slice(0, spec.mode === "choice" ? 1 : 2);
  const make = (o: ControlItem) => (hasDetails(spec) ? { value: o.value, ...exampleRow(detailsOf(spec, o)) } : o.value);
  return spec.mode === "choice" ? (pick[0] ? make(pick[0]) : undefined) : pick.map(make);
}
/** Options as a service would send them: a value, a label, and the values of the fields each option opens (same keys as the control's). */
export const exampleOptions = (spec: ControlSpec): ControlItem[] => [1, 2, 3].map((n) => ({ value: `option-${n}`, label: `Option ${n}`, ...(n === 3 ? { disabled: true } : {}), ...Object.fromEntries(Object.entries(exampleRow(spec.parts ?? [])).map(([k, v]) => [k, typeof v === "string" ? `${v} ${n}` : v])) }));
