import { describe, expect, it } from "vitest";
import { flatten, toTree } from "./OptionsDesigner";

const items = [
  { value: "saab", label: "Saab" },
  { value: "mercedes", label: "Mercedes", disabled: true },
  { value: "ford", label: "Ford", group: "US Brands" },
  { value: "plymouth", label: "Plymouth", group: "US Brands", disabled: true },
  { value: "fiat", label: "Fiat", group: "EU" },
];

describe("arbre des options", () => {
  it("range les options consécutives d'un même groupe sous un parent et redonne la liste à plat", () => {
    const tree = toTree(items);
    expect(tree.map((t) => t.kind)).toEqual(["opt", "opt", "group", "group"]);
    expect(tree[2]).toMatchObject({ kind: "group", name: "US Brands" });
    expect(flatten(tree)).toEqual(items);
  });
  it("un groupe sans nom dissout ses options, un groupe vide disparaît de la liste à plat", () => {
    const tree = toTree(items);
    if (tree[2]?.kind === "group") tree[2].name = " ";
    expect(flatten(tree).slice(2, 4)).toEqual([{ value: "ford", label: "Ford" }, { value: "plymouth", label: "Plymouth", disabled: true }]);
    expect(flatten([{ uid: 1, kind: "group", name: "Vide", children: [] }])).toEqual([]);
  });
});
