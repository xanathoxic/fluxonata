import { describe, expect, it } from "vitest";
import { interactions, optionSchema, valueSchema, fieldRows } from "./controlDocs";
import type { ControlSpec } from "./controls";

const parts = [
  { key: "nom", label: "Nom", type: "text" as const, required: true },
  { key: "age", label: "Âge", type: "number" as const, min: 0, max: 120 },
  { key: "total", label: "Total", type: "number" as const, compute: "{age} * 2" },
  { key: "titre", label: "Titre", type: "heading" as const },
];

describe("schéma de la valeur d'un contrôle", () => {
  it("groupe : un objet, champs obligatoires, bornes, calculé en lecture seule, blocs ignorés", () => {
    const s = valueSchema({ version: 1, mode: "group", parts }) as { properties: Record<string, Record<string, unknown>>; required: string[] };
    expect(Object.keys(s.properties)).toEqual(["nom", "age", "total"]);
    expect(s.required).toEqual(["nom"]);
    expect(s.properties.age).toMatchObject({ type: "number", minimum: 0, maximum: 120 });
    expect(s.properties.total).toMatchObject({ readOnly: true });
  });
  it("lignes : une liste bornée d'objets", () => {
    expect(valueSchema({ version: 1, mode: "repeat", parts, min: 1, max: 5 })).toMatchObject({ type: "array", minItems: 1, maxItems: 5, items: { type: "object" } });
  });
  it("choix sans détail : un texte limité aux options fixes, libre si elles viennent d'un service", () => {
    const spec: ControlSpec = { version: 1, mode: "choice", parts: [], items: [{ value: "a" }, { value: "b" }] };
    expect(valueSchema(spec)).toEqual({ type: "string", enum: ["a", "b"] });
    expect(valueSchema({ ...spec, dynamic: true })).toEqual({ type: "string" });
  });
  it("liste à cocher avec détails : liste d'objets {value, …détails}", () => {
    const s = valueSchema({ version: 1, mode: "items", parts, items: [{ value: "a" }], max: 2 }) as { items: { required: string[]; properties: Record<string, unknown> }; maxItems: number };
    expect(s.maxItems).toBe(2);
    expect(s.items.required).toEqual(["value", "nom"]);
    expect(Object.keys(s.items.properties)).toEqual(["value", "nom", "age", "total"]);
  });
  it("une option reçue porte les clés des champs saisissables, pas les calculés", () => {
    const s = optionSchema({ version: 1, mode: "choice", dynamic: true, parts }) as { properties: Record<string, unknown>; required: string[] };
    expect(Object.keys(s.properties)).toEqual(["value", "label", "group", "disabled", "nom", "age"]);
    expect(s.required).toEqual(["value"]);
  });
});

describe("lignes de documentation", () => {
  it("décrit chaque champ saisi", () => {
    const rows = fieldRows(parts);
    expect(rows.map((r) => r.key)).toEqual(["nom", "age", "total"]);
    expect(rows[0]).toMatchObject({ required: true, json: "texte" });
    expect(rows[2]).toMatchObject({ required: false });
    expect(rows[2]?.notes[0]).toContain("calculé");
  });
  it("les interactions suivent le type et la présence de détails", () => {
    const syntaxes = (spec: ControlSpec) => interactions(spec).map((a) => a.syntax);
    expect(syntaxes({ version: 1, mode: "group", parts })).toContain("{{ inputs.<clé>.nom }}");
    expect(syntaxes({ version: 1, mode: "repeat", parts })).toEqual(expect.arrayContaining(["{{ inputs.<clé>[0].nom }}", 'sum_col({<clé>}, "age")', "count({<clé>})"]));
    expect(syntaxes({ version: 1, mode: "choice", parts: [] })).toContain("{{ inputs.<clé> }}");
    expect(syntaxes({ version: 1, mode: "choice", parts })).toContain("{{ inputs.<clé>.value }}");
    expect(interactions({ version: 1, mode: "items", parts: [], dynamic: true }).some((a) => a.goal.includes("Fournir les options"))).toBe(true);
    expect(interactions({ version: 1, mode: "group", parts }).some((a) => a.goal.includes("Fournir les options"))).toBe(false);
  });
});
