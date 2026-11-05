import type { FormIconName } from "./FormIcon";
import { isEmpty, validateDefinition, visibleState } from "./logic";
import type { FieldOption, FormDefinition, FormField } from "./types";
import { isBlock } from "./types";

/** Custom controls: a field made of other fields (see backend/triggers/formcontrols.py, which this file mirrors).
 *  group: one set of sub-fields, the value is an object · repeat: the set repeated, a list of objects ·
 *  items: boxes to tick, each opening optional details, a list of values (or of `{value, …details}`) ·
 *  choice: one option of a drop-down or radio list, each option possibly opening its own details (a value, or `{value, …details}`).
 *  Details = the control's shared `parts` then the `parts` of the option, which may be anything, nested controls included. */
export type ControlMode = "group" | "repeat" | "items" | "choice";
/** `group`: a heading shown above consecutive options · `disabled`: shown, not selectable */
/** How a FORM FIELD pre-fills its custom control from a web service (the control itself carries no call: it is reused by many forms,
 *  which each read their own service). The options it may read are the field's `source` (a `DataSource`). `params` are fields of
 *  the form whose values fill the placeholders `{name}` of the path. */
export interface ControlPrefill { connection: string; path: string; value_path?: string; params?: string[] }
/** `data`: values an option read from a service carries for the fields it opens (by key); they pre-fill the details when it is chosen */
export interface ControlItem extends FieldOption { parts?: FormField[]; group?: string; disabled?: boolean; data?: Record<string, unknown> }
export type ControlDisplay = "cards" | "table" | "select" | "radio" | "list" | "dropdown";
export interface ControlSpec {
  version: 1;
  mode: ControlMode;
  parts: FormField[];
  items?: ControlItem[];
  items_editable?: boolean;
  min?: number;
  max?: number;
  row_label?: string;
  add_label?: string;
  /** repeat: "cards" (default) or "table" · choice: "select" (default) or "radio" */
  display?: ControlDisplay;
  /** repeat shown as a table: numeric columns totalled under it */
  totals?: string[];
  /** items / choice: the prompt of the search box of a drop-down */
  placeholder?: string;
  /** items / choice: the options are supplied by the form that uses the control (its field reads a service for them) */
  dynamic?: boolean;
}
export interface ControlRef { ref: string; version: string; name?: string; icon?: string; spec: ControlSpec }
export interface ControlCard {
  slug: string; name: string; description: string; icon: string; folder: string; used_by?: number; tags: string[]; owner: string | null; mode: ControlMode | null; parts: number;
  latest_version: { version: string; created_at: string; changelog: string; digest: string } | null;
  draft_revision: number; created_at: string; updated_at: string;
  published?: { version: string; name: string; icon: string; spec: ControlSpec };
}
export interface ControlFull extends ControlCard { spec: ControlSpec; versions: NonNullable<ControlCard["latest_version"]>[]; usage: string[] }

export const MODES: { id: ControlMode; label: string; hint: string; icon: FormIconName }[] = [
  { id: "group", label: "Groupe", hint: "Plusieurs champs réunis en un seul contrôle (une adresse, une personne…). La valeur est un objet.", icon: "layers" },
  { id: "repeat", label: "Lignes répétées", hint: "Le même groupe de champs, répété autant de fois que nécessaire (lignes de commande, invités…). La valeur est une liste.", icon: "repeat" },
  { id: "choice", label: "Liste de choix (un seul)", hint: "Un seul choix parmi une liste d'options, chacune pouvant demander les mêmes champs de détail. La valeur est le choix (et ses détails).", icon: "list2" },
  { id: "items", label: "Liste de choix (plusieurs)", hint: "Plusieurs choix cochés dans une liste, chacun pouvant demander les mêmes champs de détail. La valeur est une liste.", icon: "boxes" },
];
export const MAX_ROWS = 50;
export const MAX_ITEMS = 100;
/** The icons a control may carry (the form editor shows it on the field's card and in the palette). */
export const CONTROL_ICONS: FormIconName[] = ["custom", "layers", "repeat", "boxes", "checkbox_list", "user", "mapPin", "phone", "email", "date", "euro", "cart", "building", "file", "star", "heart", "tag", "list", "link", "clock"];
const KNOWN = new Set<string>(CONTROL_ICONS);
export const controlIcon = (icon: string | undefined): FormIconName => (icon && KNOWN.has(icon) ? (icon as FormIconName) : "custom");
export const emptySpec = (mode: ControlMode = "group"): ControlSpec => ({ version: 1, mode, parts: [], ...(mode === "items" || mode === "choice" ? { items: [{ value: "option-1", label: "Option 1" }] } : {}) });
/** The JSON type the control hands to the workflow. */
export const hasDetails = (spec: ControlSpec): boolean => [...(spec.parts ?? []), ...(spec.items ?? []).flatMap((o) => o.parts ?? [])].some((p) => !isBlock(p.type));
export const producesOf = (spec: ControlSpec): string => (spec.mode === "choice" ? (hasDetails(spec) ? "object" : "string") : spec.mode === "group" ? "object" : "array");
/** Options of the same group side by side (at the place of the group's first option): fixed options and options from a service are
 *  merged, so a group may otherwise appear twice. The order inside a group, and of the groups, is kept. */
export function regroup(items: ControlItem[]): ControlItem[] {
  const order: string[] = [];
  const by = new Map<string, ControlItem[]>();
  for (const o of items) {
    const g = o.group ?? "";
    if (!by.has(g)) { by.set(g, []); order.push(g); }
    (by.get(g) as ControlItem[]).push(o);
  }
  return order.flatMap((g) => by.get(g) as ControlItem[]);
}
/** The details an option pre-fills when chosen: its `data`, for the fields it opens only. */
export const seedOf = (spec: ControlSpec, item?: ControlItem): Record<string, unknown> => {
  const keys = new Set(detailsOf(spec, item).filter((p) => !isBlock(p.type) && !p.compute).map((p) => p.key));
  return Object.fromEntries(Object.entries(item?.data ?? {}).filter(([k]) => keys.has(k)));
};
/** The sub-fields an option opens: the control's shared ones, then its own. */
export const detailsOf = (spec: ControlSpec, item?: ControlItem): FormField[] => [...(spec.parts ?? []), ...(item?.parts ?? [])];

/** The parts of one line, as a small form (the form engine applies rules, calculations and checks to it). */
export const rowDefinition = (parts: FormField[] | undefined): FormDefinition => ({ version: 1, sections: [{ id: "row", title: "", fields: parts ?? [] }] });

/** The boxes of an « items » control: the field's own list when the control lets the form author edit it. */
export const itemsOf = (field: FormField): ControlItem[] => (field.control?.spec.items_editable && field.options ? field.options : field.control?.spec.items ?? []);

const compact = (row: Record<string, unknown>): Record<string, unknown> => Object.fromEntries(Object.entries(row).filter(([, v]) => !isEmpty(v) && !(typeof v === "object" && v !== null && !Array.isArray(v) && Object.keys(v).length === 0)));
const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

function row(parts: FormField[], value: unknown, prefix: string): { kept: Record<string, unknown> | null; problem: string } {
  if (!isObject(value)) return { kept: null, problem: `${prefix}Un objet est attendu.` };
  const def = rowDefinition(parts);
  const kept = compact(visibleState(def, value).kept);
  const errors = validateDefinition(def, value);
  const label = (k: string) => parts.find((p) => p.key === k)?.label || k;
  const first = Object.entries(errors)[0];
  return { kept, problem: first ? `${prefix}${label(first[0])} : ${first[1]}` : "" };
}

/** The option a value stands for. When the form field reads a service for its options, a value the fixed list does not know is a dynamic option (the server never
 *  calls the service while a form is submitted: only the shape of the value is checked). */
function known(byValue: Map<string, ControlItem>, field: FormField, v: unknown): ControlItem | undefined {
  if (typeof v !== "string") return undefined;
  return byValue.get(v) ?? (field.source && v.length > 0 && v.length <= 200 ? { value: v } : undefined);
}

function processChoice(field: FormField, spec: ControlSpec, value: unknown): { value: unknown; problem: string } {
  const raw = typeof value === "string" ? { value } : value;
  if (raw === undefined || raw === null) return { value: {}, problem: field.required ? "Ce champ est obligatoire." : "" };
  if (!isObject(raw)) return { value, problem: "Un choix est attendu." };
  const v = raw.value;
  if (v === undefined || v === null || v === "") return { value: {}, problem: field.required ? "Ce champ est obligatoire." : "" };
  const item = known(new Map(itemsOf(field).map((o) => [o.value, o])), field, v);
  if (typeof v !== "string" || !item) return { value, problem: "Choix non proposé." };
  if (item.disabled) return { value, problem: "Choix indisponible." };
  if (!hasDetails(spec)) return { value: v, problem: "" };
  const extra = Object.fromEntries(Object.entries(raw).filter(([k]) => k !== "value"));
  const r = row(detailsOf(spec, item), extra, `${item.label || v} — `);
  return r.problem ? { value, problem: r.problem } : { value: { value: v, ...(r.kept ?? {}) }, problem: "" };
}

/** The value as the server keeps it (hidden and calculated sub-values, blank lines) and a message if it is not acceptable. */
export function processControl(field: FormField, value: unknown): { value: unknown; problem: string } {
  const spec = field.control?.spec;
  if (!spec) return { value, problem: "Contrôle inconnu." };
  if (spec.mode === "group") {
    if (!isObject(value)) return { value, problem: "Un objet est attendu." };
    const r = row(spec.parts ?? [], value, "");
    return { value: r.kept, problem: r.problem };
  }
  if (spec.mode === "choice") return processChoice(field, spec, value);
  if (!Array.isArray(value)) return { value, problem: "Une liste est attendue." };
  const cap = spec.mode === "repeat" ? MAX_ROWS : MAX_ITEMS;
  const floor = Math.max(spec.min ?? 0, field.required ? 1 : 0);
  const ceiling = spec.max ?? cap;
  if (value.length > cap * 2) return { value, problem: "Trop d'éléments." };
  let result: unknown[];
  let what: string;
  if (spec.mode === "repeat") {
    const rows: Record<string, unknown>[] = [];
    for (let i = 0; i < value.length; i++) {
      const raw = value[i];
      if (isObject(raw) && Object.values(raw).every((v) => v === null || v === undefined || v === "" || v === false || (Array.isArray(v) && v.length === 0) || (isObject(v) && Object.keys(v).length === 0))) continue;
      const r = row(spec.parts ?? [], raw, `Ligne ${i + 1} — `);
      if (r.problem) return { value: rows, problem: r.problem };
      if (r.kept && Object.keys(r.kept).length) rows.push(r.kept);
    }
    result = rows; what = "ligne";
  } else {
    const items = itemsOf(field);
    const byValue = new Map(items.map((o) => [o.value, o]));
    const details = hasDetails(spec);
    const picked = new Map<string, Record<string, unknown>>();
    for (const raw of value) {
      const v = isObject(raw) ? raw.value : raw;
      const item = known(byValue, field, v);
      if (typeof v !== "string" || !item) return { value, problem: "Choix non proposé." };
      if (picked.has(v)) return { value, problem: "Choix en double." };
      if (item.disabled) return { value, problem: "Choix indisponible." };
      if (details) {
        const extra = isObject(raw) ? Object.fromEntries(Object.entries(raw).filter(([k]) => k !== "value")) : {};
        const r = row(detailsOf(spec, item), extra, `${item.label || v} — `);
        if (r.problem) return { value, problem: r.problem };
        picked.set(v, { value: v, ...(r.kept ?? {}) });
      } else picked.set(v, { value: v });
    }
    const ordered = [...items.filter((o) => picked.has(o.value)).map((o) => picked.get(o.value) as Record<string, unknown>), ...[...picked].filter(([k]) => !byValue.has(k)).map(([, p]) => p)];
    result = details ? ordered : ordered.map((p) => p.value);
    what = "choix";
  }
  if (result.length < floor) return { value: result, problem: `Au moins ${floor} ${what}(s).` };
  if (result.length > ceiling) return { value: result, problem: `Au plus ${ceiling} ${what}(s).` };
  return { value: result, problem: "" };
}

/** A field of a form for a published control: a copy of its description travels with the field. */
export function fieldForControl(card: Required<Pick<ControlCard, "slug" | "published">>, key: string): FormField {
  const { version, name, icon, spec } = card.published;
  return {
    key, label: name, type: "custom", control: { ref: card.slug, version, name, icon, spec: JSON.parse(JSON.stringify(spec)) as ControlSpec },
    ...(spec.mode === "items" && spec.items_editable ? { options: JSON.parse(JSON.stringify(spec.items ?? [])) as FieldOption[] } : {}),
  };
}
