import { describe, expect, it } from "vitest";
import { fieldName, fieldsToSchema, schemaToFields, slugifyName } from "./fields";

describe("skill fields <-> JSON schema", () => {
  it("builds an object schema with required inputs only when asked", () => {
    const f = [{ name: "texte", type: "string" as const, required: true, description: "Le texte" }, { name: "n", type: "integer" as const, required: false, description: "" }];
    expect(fieldsToSchema(f, true)).toEqual({ type: "object", required: ["texte"], properties: { texte: { type: "string", description: "Le texte" }, n: { type: "integer" } } });
    // a model answer must always carry every field
    expect(fieldsToSchema(f, false).required).toEqual(["texte", "n"]);
  });

  it("round-trips and ignores unnamed rows", () => {
    const schema = { type: "object", required: ["a"], properties: { a: { type: "boolean", description: "d" }, b: { type: "number" } } };
    expect(fieldsToSchema(schemaToFields(schema), true)).toEqual(schema);
    expect(fieldsToSchema([{ name: "", type: "string", required: true, description: "" }], true).properties).toEqual({});
  });

  it("normalises names to what the server accepts", () => {
    expect(fieldName("Résumé court!")).toBe("resume_court_");
    expect(fieldName("1abc")).toBe("abc");
    expect(slugifyName("Qualifier un texte")).toBe("qualifier-un-texte");
  });
});
