import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { processControl, producesOf, regroup, rowDefinition, type ControlSpec } from "./controls";
import { validateDefinition, visibleState } from "./logic";
import type { FormDefinition, FormField } from "./types";

const CASES = JSON.parse(readFileSync(resolve(process.cwd(), "..", "backend", "tests", "fixtures", "form_control_cases.json"), "utf8")) as { name: string; field: FormField; value: unknown; expected: { value: unknown; problem: string } }[];

describe("contrôles personnalisés : les mêmes cas que le serveur", () => {
  for (const c of CASES) it(c.name, () => expect(processControl(c.field, c.value)).toEqual({ value: c.expected.value, problem: c.expected.problem }));
});

describe("contrôle dans un formulaire", () => {
  const lines: ControlSpec = { version: 1, mode: "repeat", parts: [{ key: "qte", label: "Qté", type: "number", required: true }, { key: "prix", label: "Prix", type: "number" }, { key: "total", label: "Total", type: "number", compute: "{qte} * {prix}" }] };
  const f = (key: string, extra: Partial<FormField>) => ({ key, label: key, type: "text", ...extra }) as FormField;
  const def = { version: 1, sections: [{ id: "s", title: "S", fields: [
    f("lignes", { type: "custom", control: { ref: "l", version: "1.0.0", spec: lines } }),
    f("nb", { type: "number", compute: "count({lignes})" }),
    f("somme", { type: "number", compute: 'sum_col({lignes}, "total")' }),
  ] }] } as FormDefinition;

  it("calcule les lignes, les compte et les totalise", () => {
    const { kept } = visibleState(def, { lignes: [{ qte: 2, prix: 5 }, { qte: 1, prix: 3 }] });
    expect(kept).toEqual({ lignes: [{ qte: 2, prix: 5, total: 10 }, { qte: 1, prix: 3, total: 3 }], nb: 2, somme: 13 });
  });
  it("signale la ligne fautive", () => {
    expect(validateDefinition(def, { lignes: [{ qte: 1, prix: 1 }, { prix: 2 }] }).lignes).toBe("Ligne 2 — Qté : Ce champ est obligatoire.");
  });
  it("type de la valeur remise au workflow", () => {
    expect(producesOf(lines)).toBe("array");
    expect(producesOf({ ...lines, mode: "group" })).toBe("object");
    expect(rowDefinition(lines.parts).sections[0]?.fields).toHaveLength(3);
  });
});

describe("un contrôle sans sous-champs", () => {
  it("donne une ligne vide plutôt qu'une erreur (liste à cocher sans détail)", () => {
    expect(rowDefinition(undefined).sections[0]?.fields).toEqual([]);
  });
});

describe("options d'un même groupe", () => {
  it("rapproche celles d'un service de celles du contrôle, sans changer l'ordre dans un groupe", () => {
    const merged = [{ value: "a" }, { value: "us1", group: "US" }, { value: "b" }, { value: "us2", group: "US" }, { value: "c", group: "EU" }];
    expect(regroup(merged).map((o) => o.value)).toEqual(["a", "b", "us1", "us2", "c"]);
  });
});
