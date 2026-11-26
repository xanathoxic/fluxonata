import { describe, expect, it } from "vitest";
import { sampleFromSchema } from "./ActionIde";

describe("sampleFromSchema", () => {
  it("prend la valeur par défaut, sinon le zéro du type", () => {
    const text = JSON.stringify({
      type: "object",
      properties: {
        n: { type: "integer" },
        ok: { type: "boolean" },
        nom: { type: "string", default: "x" },
        l: { type: "array" },
        o: { type: "object" },
        s: { type: "string" },
      },
    });
    expect(JSON.parse(sampleFromSchema(text))).toEqual({ n: 0, ok: false, nom: "x", l: [], o: {}, s: "" });
  });
  it("renvoie un objet vide pour un schéma sans propriétés ou illisible", () => {
    expect(sampleFromSchema('{"type":"object"}')).toBe("{}");
    expect(sampleFromSchema("pas du json")).toBe("{}");
  });
});
