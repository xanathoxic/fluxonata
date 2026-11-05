import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { applyRules, canHide, canLoad, describeEffect, evalNode, gatedLoads, evaluationOrder, initialState, readers, render, sentence, textOf, type Node, type Rule } from "./rules";
import { visibleState } from "./logic";
import type { FormField } from "./types";

const load = (name: string) => JSON.parse(readFileSync(resolve(process.cwd(), "..", "backend", "tests", "fixtures", name), "utf8")) as { name: string }[];
const CASES = load("form_rules_cases.json") as unknown as { name: string; cond: Node; values: Record<string, unknown>; expected: boolean }[];
const STATES = load("form_rules_states.json") as unknown as { name: string; field: FormField; values: Record<string, unknown>; expected: Record<string, unknown> }[];

describe("conditions: the same cases as the server", () => {
  for (const c of CASES) it(c.name, () => expect(evalNode(c.cond, c.values)).toBe(c.expected));
});
describe("field states: the same cases as the server", () => {
  for (const c of STATES) it(c.name, () => {
    const s = applyRules(c.field, c.values, initialState(c.field));
    for (const [k, v] of Object.entries(c.expected)) expect((s as unknown as Record<string, unknown>)[k], k).toEqual(v);
  });
});

describe("texts", () => {
  it("fills {fields} in a template like the server does", () => {
    expect(render("Bonjour {nom} ({age} ans)", { nom: "Ada", age: 36 })).toBe("Bonjour Ada (36 ans)");
    expect(render("{x}{y}", { x: ["a", "b"], y: true })).toBe("a,btrue");
    expect(render("{inconnu} reste vide", {})).toBe(" reste vide");
    expect(render("pas de jeton { nom }", { nom: "Ada" })).toBe("pas de jeton { nom }");
    expect(textOf(5)).toBe("5");
  });
  it("applies text effects", () => {
    const f: FormField = { key: "x", label: "X", type: "text", rules: [{ when: { field: "a", op: "not_empty" }, then: [{ do: "text", target: "label", text: "Nom de {a}" }, { do: "text", target: "help", text: "Aide" }] }] as Rule[] };
    const s = applyRules(f, { a: "Ada" }, initialState(f));
    expect([s.label, s.help, s.placeholder]).toEqual(["Nom de Ada", "Aide", null]);
  });
  it("knows when a field can be hidden", () => {
    expect(canHide({ rules: [{ when: { field: "a", op: "not_empty" }, then: [{ do: "hide" }] }] })).toBe(true);
    expect(canHide({ rules: [{ when: { field: "a", op: "not_empty" }, then: [{ do: "require" }] }] })).toBe(false);
  });
});

describe("sentences", () => {
  const label = (k: string) => ({ pays: "Pays", age: "Âge", mail: "E-mail" })[k] ?? k;
  it("writes a condition tree in French", () => {
    const tree: Node = { op: "all", items: [{ field: "pays", op: "equals", value: "FR", ci: true }, { op: "any", items: [{ field: "age", op: "gte", value: 18 }, { field: "mail", op: "ends_with", value: ".fr" }] }] };
    expect(sentence(tree, label)).toBe("Pays est égal à « FR » (sans tenir compte de la casse) ET (Âge est supérieur ou égal à 18 OU E-mail finit par « .fr »)");
    expect(sentence({ field: "mail", op: "is_empty" }, label)).toBe("E-mail est vide");
    expect(sentence({ field: "pays", op: "in_list", values: ["FR", "BE"] }, label)).toBe("Pays est l'une de « FR », « BE »");
    expect(sentence({ field: "age", op: "gt", ref: "pays" }, label)).toBe("Âge est supérieur à la valeur de « Pays »");
  });
});

describe("a field may read any other field, wherever it is laid out", () => {
  const hideIf = (other: string): Rule[] => [{ when: { field: other, op: "equals", value: "x" }, then: [{ do: "hide" }] }];
  const def = { version: 1, sections: [{ id: "s", title: "S", fields: [
    { key: "c", label: "C", type: "text", layout: { row: 1, col: 1, span: 6 }, rules: hideIf("b") },
    { key: "b", label: "B", type: "text", layout: { row: 1, col: 7, span: 6 }, rules: hideIf("a") },
    { key: "a", label: "A", type: "text", layout: { row: 2, col: 1, span: 12 } },
  ] }] } as unknown as Parameters<typeof visibleState>[0];

  it("evaluates what a field reads before the field", () => {
    const fields = def.sections[0]!.fields;
    expect(evaluationOrder(fields).map((f) => f.key)).toEqual(["a", "b", "c"]);
    // a = x hides b, so c (which reads the hidden, dropped b) stays shown: same as the server test
    const { shown, kept } = visibleState(def, { a: "x", b: "x", c: "v" });
    expect([...shown].sort()).toEqual(["a", "c"]);
    expect(kept).toEqual({ a: "x", c: "v" });
  });

  it("knows which fields would make a loop", () => {
    const fields = def.sections[0]!.fields;
    expect([...readers(fields, "a")].sort()).toEqual(["b", "c"]);
    expect([...readers(fields, "c")]).toEqual([]);
  });
});

describe("remplir le champ", () => {
  const rule = (value: string): Rule[] => [{ when: { field: "a", op: "equals", value: "FR" }, then: [{ do: "fill", value }] }];
  it("rend la valeur avec les champs insérés, seulement quand la règle s'applique", () => {
    const f = { key: "x", type: "text", rules: rule("{a}-{b}") } as FormField;
    expect(applyRules(f, { a: "FR", b: "7" }, initialState(f)).fill).toBe("FR-7");
    expect(applyRules(f, { a: "DE" }, initialState(f)).fill).toBeNull();
  });
  it("convertit comme le serveur et l'impose à la valeur gardée", () => {
    const def = { version: 1, sections: [{ id: "s", title: "S", fields: [
      { key: "a", label: "A", type: "text", layout: { row: 1, col: 1, span: 6 } },
      { key: "x", label: "X", type: "number", layout: { row: 1, col: 7, span: 6 }, rules: rule("12,5") },
    ] }] } as unknown as Parameters<typeof visibleState>[0];
    expect(visibleState(def, { a: "FR", x: 1 }).kept).toEqual({ a: "FR", x: 12.5 });
    expect(visibleState(def, { a: "DE", x: 1 }).kept).toEqual({ a: "DE", x: 1 });
  });
});

describe("chargement d'un service piloté par les règles", () => {
  const field = { rules: [{ when: { field: "pays", op: "equals", value: "FR" }, then: [{ do: "load", what: "options" }] }] } as unknown as FormField;
  it("sans règle de chargement, tout charge tout seul ; avec une règle, seulement tant qu'elle s'applique", () => {
    expect(canLoad({}, undefined, "options")).toBe(true);
    expect(canLoad(field, applyRules(field, { pays: "FR" }, initialState({})), "options")).toBe(true);
    expect(canLoad(field, applyRules(field, { pays: "BE" }, initialState({})), "options")).toBe(false);
    expect(canLoad(field, applyRules(field, { pays: "BE" }, initialState({})), "prefill")).toBe(true); // aucune règle ne la nomme
    expect([...gatedLoads(field)]).toEqual(["options"]);
    expect(describeEffect({ do: "load", what: "prefill" })).toContain("précharger");
  });
});
