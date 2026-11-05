import type { JsonSchema } from "../api";
import { producesOf } from "./controls";
import { canHide } from "./rules";
import { allFields, isBlock, PRODUCES, type FormDefinition, type FormField } from "./types";

/** The correspondence between a workflow's inputs and the fields of the form that feeds them: `{input: field key}`, "" = no field.
 *  Mirrors triggers/formengine.py (`input_source`, `check_against_inputs`, `map_inputs`): an input not listed takes the field of the
 *  same name. With no correspondence at all, the values of the form are the inputs. */
export type InputMap = Record<string, string>;

export const dataFields = (def: FormDefinition): FormField[] => allFields(def).filter((f) => !isBlock(f.type));
export const produces = (f: FormField): string => (f.type === "custom" && f.control ? producesOf(f.control.spec) : PRODUCES[f.type as keyof typeof PRODUCES] ?? "string");
const wanted = (p: JsonSchema | undefined): string | undefined => (Array.isArray(p?.type) ? p?.type.find((t) => t !== "null") : p?.type);
const compatible = (want: string | undefined, have: string) => !want || want === have || (want === "integer" && have === "number");
const hasEntries = (map: InputMap | undefined): map is InputMap => Boolean(map) && Object.keys(map as InputMap).length > 0;

/** The key of the field that feeds the input `name`, or null. */
export function sourceOf(map: InputMap | undefined, name: string, keys: Set<string>): string | null {
  if (map && name in map) return map[name] || null;
  return keys.has(name) ? name : null;
}

/** The inputs of the workflow from the values of the form (by field key). */
export function toInputs(def: FormDefinition, schema: JsonSchema | undefined, map: InputMap | undefined, values: Record<string, unknown>): Record<string, unknown> {
  if (!hasEntries(map) || !schema?.properties) return values;
  const keys = new Set(dataFields(def).map((f) => f.key));
  const out: Record<string, unknown> = {};
  for (const name of Object.keys(schema.properties)) {
    const key = sourceOf(map, name, keys);
    if (key !== null && key in values) out[name] = values[key];
  }
  return out;
}

/** An error the server reports on an input, shown on the field that feeds it. */
export function errorsToFields(def: FormDefinition, schema: JsonSchema | undefined, map: InputMap | undefined, errors: Record<string, string>): Record<string, string> {
  if (!hasEntries(map) || !schema?.properties) return errors;
  const keys = new Set(dataFields(def).map((f) => f.key));
  return Object.fromEntries(Object.entries(errors).map(([name, text]) => [sourceOf(map, name, keys) ?? name, text]));
}

/** A correspondence to start from: same key, else same label as the input's title. */
export function autoMap(def: FormDefinition, schema: JsonSchema | undefined): InputMap {
  const fields = dataFields(def);
  const norm = (t: string | undefined) => (t ?? "").trim().toLowerCase();
  const out: InputMap = {};
  for (const [name, prop] of Object.entries(schema?.properties ?? {})) {
    const hit = fields.find((f) => f.key === name) ?? fields.find((f) => norm(f.label) !== "" && (norm(f.label) === norm(prop.title) || norm(f.label) === norm(name)));
    out[name] = hit?.key ?? "";
  }
  return out;
}

/** What is wrong with a correspondence, by input (as the server checks it when the workflow is published). */
export function problemsOf(def: FormDefinition, schema: JsonSchema | undefined, map: InputMap | undefined): Record<string, string> {
  const props = schema?.properties ?? {};
  const required = new Set(schema?.required ?? []);
  const fields = new Map(dataFields(def).map((f) => [f.key, f]));
  const out: Record<string, string> = {};
  if (!hasEntries(map)) {
    for (const key of fields.keys()) if (!(key in props)) out[key] = `Le workflow n'a pas d'entrée « ${key} ».`;
  } else {
    for (const name of Object.keys(map)) if (!(name in props)) out[name] = `Le workflow n'a pas d'entrée « ${name} ».`;
  }
  for (const [name, prop] of Object.entries(props)) {
    if (name in out) continue;
    const key = sourceOf(map, name, new Set(fields.keys()));
    if (key === null) { if (required.has(name) && prop.default === undefined) out[name] = "Entrée obligatoire : choisissez le champ qui la remplit."; continue; }
    const f = fields.get(key);
    if (!f) { out[name] = `Le champ « ${key} » n'existe pas (ou plus) dans ce formulaire.`; continue; }
    const have = produces(f);
    if (!compatible(wanted(prop), have)) out[name] = `L'entrée attend « ${wanted(prop)} » ; ce champ produit « ${have} ».`;
    else if (required.has(name) && (f.visible_if || canHide(f))) out[name] = "Une entrée obligatoire ne peut pas être masquée par une règle.";
  }
  return out;
}
