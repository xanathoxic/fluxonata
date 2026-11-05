import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { check, evaluate, refs } from "./calc";
import { validateDefinition, visibleState } from "./logic";
import { evaluationOrder } from "./rules";
import type { FormDefinition, FormField } from "./types";

const CASES = JSON.parse(readFileSync(resolve(process.cwd(), "..", "backend", "tests", "fixtures", "form_calc_cases.json"), "utf8")) as { name: string; formula: string; values: Record<string, unknown>; expected: unknown }[];

describe("formules : les mêmes cas que le serveur", () => {
  for (const c of CASES) it(c.name, () => expect(evaluate(c.formula, c.values)).toEqual(c.expected));
});

describe("formules illisibles", () => {
  for (const f of ["", "1 +", "(1 + 2", "1 2", "inconnue(1)", "round()", "round(1, 2, 3)", "if(1, 2)", "abc", "{a} $ 2", '"ouvert', "{1a}", "x".repeat(501), `${"(".repeat(25)}1${")".repeat(25)}`, Array(120).fill("1").join(" + ")])
    it(JSON.stringify(f.slice(0, 30)), () => expect(check(f, new Set(["a"]))).not.toBe(""));
  it("lit les champs d'une formule et refuse un champ inconnu", () => {
    expect([...refs("{a} * {b} + len({c})")].sort()).toEqual(["a", "b", "c"]);
    expect(check("{a} + {b}", new Set(["a"]))).toBe("« b » doit être un autre champ du formulaire.");
    expect(check("{a} + 1", new Set(["a"]))).toBe("");
  });
});

describe("champ calculé dans le formulaire", () => {
  const f = (key: string, extra: Partial<FormField>, row: number, col = 1, span = 12) => ({ key, label: key, type: "number", layout: { row, col, span }, ...extra }) as FormField;
  const def = { version: 1, sections: [{ id: "s", title: "S", fields: [
    f("total", { compute: "{ht} * 1.2", required: true }, 1),
    f("ht", { compute: "{prix} * {qte}" }, 2, 1, 6),
    f("prix", {}, 3, 1, 6),
    f("qte", {}, 3, 7, 6),
  ] }] } as FormDefinition;

  it("évalue d'abord ce que le champ lit, où qu'il soit placé", () => {
    expect(evaluationOrder(def.sections[0]!.fields).map((x) => x.key)).toEqual(["prix", "qte", "ht", "total"]);
    expect(visibleState(def, { prix: 10, qte: 2, total: 1 }).kept).toEqual({ prix: 10, qte: 2, ht: 20, total: 24 });
  });
  it("sans valeur calculable : rien n'est gardé et l'obligation joue", () => {
    expect(visibleState(def, { prix: 10 }).kept).toEqual({ prix: 10 });
    expect(validateDefinition(def, { prix: 10 })).toEqual({ total: "Ce champ est obligatoire." });
  });
});
