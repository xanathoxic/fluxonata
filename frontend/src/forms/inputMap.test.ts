import { describe, expect, it } from "vitest";
import type { JsonSchema } from "../api";
import { autoMap, errorsToFields, problemsOf, sourceOf, toInputs } from "./inputMap";
import type { FormDefinition } from "./types";

const def: FormDefinition = { version: 1, sections: [{ title: "", fields: [
  { key: "objet", label: "Sujet", type: "text" }, { key: "raison", label: "Raison", type: "text" }, { key: "annee", label: "Âge", type: "number" }, { key: "note", label: "Note", type: "text" },
] }] };
const schema: JsonSchema = { type: "object", required: ["sujet"], properties: { sujet: { type: "string", title: "Sujet" }, motif: { type: "string" }, age: { type: "integer", title: "Âge" } } };
const map = { sujet: "objet", motif: "raison", age: "annee" };

describe("correspondance entrées du workflow ↔ champs du formulaire", () => {
  it("les entrées reçoivent les valeurs des champs choisis, le reste est laissé de côté", () => {
    expect(toInputs(def, schema, map, { objet: "Panne", annee: 30, note: "x" })).toEqual({ sujet: "Panne", age: 30 });
  });
  it("sans correspondance, les valeurs sont les entrées ; une entrée non listée prend le champ du même nom", () => {
    const values = { a: 1 };
    expect(toInputs(def, schema, undefined, values)).toBe(values);
    expect(toInputs(def, schema, {}, values)).toBe(values);
    const same: FormDefinition = { sections: [{ title: "", fields: [{ key: "sujet", label: "S", type: "text" }, { key: "annee", label: "A", type: "number" }] }] };
    expect(toInputs(same, schema, { age: "annee" }, { sujet: "S", annee: 4 })).toEqual({ sujet: "S", age: 4 });
    expect(sourceOf({ sujet: "" }, "sujet", new Set(["sujet"]))).toBeNull();
  });
  it("une erreur du serveur sur une entrée est donnée au champ qui la remplit", () => {
    expect(errorsToFields(def, schema, map, { sujet: "Trop court", _: "Autre" })).toEqual({ objet: "Trop court", _: "Autre" });
  });
  it("propose une correspondance : même clé, sinon même libellé", () => {
    expect(autoMap(def, schema)).toEqual({ sujet: "objet", motif: "", age: "annee" });
  });
  it("signale ce que le serveur refuserait", () => {
    expect(problemsOf(def, schema, map)).toEqual({});
    expect(problemsOf(def, schema, undefined)).toMatchObject({ objet: expect.stringContaining("n'a pas d'entrée"), sujet: expect.stringContaining("obligatoire") });
    expect(problemsOf(def, schema, { ...map, sujet: "absent", age: "raison" })).toMatchObject({ sujet: expect.stringContaining("absent"), age: expect.stringContaining("integer") });
    expect(problemsOf(def, schema, { ...map, sujet: "" }).sujet).toContain("obligatoire");
    expect(problemsOf(def, schema, { ...map, motif: "" })).toEqual({});
    expect(problemsOf(def, schema, { ...map, zzz: "objet" }).zzz).toContain("n'a pas d'entrée");
  });
});
