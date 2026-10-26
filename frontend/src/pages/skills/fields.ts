import type { JsonSchema } from "../../api";

export type FieldType = "string" | "integer" | "number" | "boolean";
export interface Field { name: string; type: FieldType; required: boolean; description: string }
export const FIELD_TYPES: [FieldType, string][] = [["string", "Texte"], ["integer", "Entier"], ["number", "Nombre"], ["boolean", "Oui / non"]];

export const slugifyName = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 50);
/** A valid technical field name: starts with a lowercase letter, then lowercase letters, digits and "_" (what the server accepts). */
export const fieldName = (s: string) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9_]/g, "_").replace(/^[^a-z]+/, "").slice(0, 40);

export function schemaToFields(schema?: JsonSchema): Field[] {
  const req = new Set(schema?.required ?? []);
  return Object.entries(schema?.properties ?? {}).map(([name, p]) => ({
    name, type: ((Array.isArray(p.type) ? p.type[0] : p.type) as FieldType) ?? "string", required: req.has(name), description: p.description ?? "",
  }));
}

/** Fields -> flat object schema. `withRequired`: inputs may be optional; a model answer must always carry every field. */
export function fieldsToSchema(fields: Field[], withRequired: boolean): JsonSchema {
  const named = fields.filter((f) => f.name);
  return {
    type: "object",
    required: named.filter((f) => !withRequired || f.required).map((f) => f.name),
    properties: Object.fromEntries(named.map((f) => [f.name, { type: f.type, ...(f.description ? { description: f.description } : {}) }])),
  };
}
