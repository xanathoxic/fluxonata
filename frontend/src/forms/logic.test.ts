import { describe, expect, it } from "vitest";
import { exprToRuleSet, ruleHolds, ruleSetToExpr, validateDefinition, visibleState } from "./logic";
import type { FormDefinition } from "./types";

const def: FormDefinition = { sections: [{ title: "S", fields: [
  { key: "type", label: "Type", type: "select", required: true, options: [{ value: "a" }, { value: "autre" }] },
  { key: "precision", label: "Précision", type: "text", required: true, visible_if: "values.type == 'autre'" },
  { key: "mail", label: "Mail", type: "email" },
  { key: "n", label: "N", type: "number", min: 1, max: 5 },
] }] };

describe("rules", () => {
  const known = new Set(["a", "b"]);
  it("evaluates comparisons, and/or/not, lists and parentheses", () => {
    expect(ruleHolds("values.a == 'x' and values.b > 2", { a: "x", b: 3 }, known)).toBe(true);
    expect(ruleHolds("values.a == 'x' and values.b > 2", { a: "x", b: 1 }, known)).toBe(false);
    expect(ruleHolds("not (values.a == 'x') or values.b == 1", { a: "x", b: 1 }, known)).toBe(true);
    expect(ruleHolds("values.a in ['x', 'y']", { a: "y" }, known)).toBe(true);
    expect(ruleHolds("values.a contains 'bc'", { a: "abcd" }, known)).toBe(true);
    expect(ruleHolds("values.a == 'it\\'s'", { a: "it's" }, known)).toBe(true);
  });
  it("hides on anything unreadable, like the server", () => {
    expect(ruleHolds("values.a >", { a: 1 }, known)).toBe(false);
    expect(ruleHolds("values.zzz == 1", { zzz: 1 }, known)).toBe(false);
    expect(ruleHolds("inputs.a == 1", { a: 1 }, known)).toBe(false);
    expect(ruleHolds("values.a < 3", { a: null }, known)).toBe(false);
    expect(ruleHolds("values.a == null", {}, known)).toBe(true);
  });
  it("round-trips the builder's structured rules", () => {
    const rs = { join: "or" as const, rules: [{ field: "a", op: "==" as const, value: "it's" }, { field: "b", op: ">=" as const, value: 3 }] };
    expect(ruleSetToExpr(rs)).toBe("values.a == 'it\\'s' or values.b >= 3");
    expect(exprToRuleSet(ruleSetToExpr(rs))).toEqual(rs);
    expect(exprToRuleSet("values.a == 1 and values.b == 2 or values.c == 3")).toBeNull();
    expect(exprToRuleSet("not values.a")).toBeNull();
  });
});

describe("visibility and validation", () => {
  it("drops the value of a hidden field and does not ask for it", () => {
    const { shown, kept } = visibleState(def, { type: "a", precision: "secret" });
    expect(shown.has("precision")).toBe(false);
    expect(kept).toEqual({ type: "a" });
    expect(validateDefinition(def, { type: "a" })).toEqual({});
  });
  it("asks for a shown required field and checks formats", () => {
    expect(validateDefinition(def, { type: "autre", mail: "x", n: 9 })).toEqual({
      precision: "Ce champ est obligatoire.", mail: "Adresse e-mail invalide.", n: "Au plus 5.",
    });
    expect(validateDefinition(def, { type: "zzz" }).type).toBe("Choix non proposé.");
  });
});
