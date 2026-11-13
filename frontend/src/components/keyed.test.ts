import { render, screen } from "@testing-library/react";
import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { keyed } from "./keyed";

describe("keyed : clés stables dérivées du contenu", () => {
  it("distingue les doublons par rang d'occurrence et ne dépend pas de la position des autres éléments", () => {
    const a = keyed(["x", "y", "x"], (s) => s);
    expect(a.map((k) => k.key)).toEqual(["x#0", "y#0", "x#1"]);
    const b = keyed(["y", "x", "x"], (s) => s); // réordonnée : « y » garde sa clé, les « x » gardent les leurs
    expect(b.map((k) => k.key).sort()).toEqual(a.map((k) => k.key).sort());
  });
  it("garde les éléments d'origine et accepte une liste vide", () => {
    const items = [{ m: "a" }, { m: "a" }];
    expect(keyed(items, (i) => i.m).map((k) => k.item)).toEqual(items);
    expect(keyed([], () => "")).toEqual([]);
  });
});

const List = ({ items }: { items: string[] }) =>
  createElement("ul", null, keyed(items, (s) => s).map(({ item, key }) => createElement("li", { key }, createElement("input", { "aria-label": item, defaultValue: item }))));

describe("keyed : stabilité dans une liste rendue", () => {
  it("garde les éléments du DOM quand une valeur est insérée, retirée ou changée, et accepte les doublons sans alerte", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { rerender } = render(createElement(List, { items: ["a", "b", "a"] }));
    const a0 = screen.getAllByLabelText("a")[0] as HTMLInputElement;
    const b = screen.getByLabelText("b") as HTMLInputElement;
    b.value = "saisi"; // état du DOM qui doit survivre
    rerender(createElement(List, { items: ["z", "a", "b", "a"] })); // insertion en tête
    expect(screen.getByLabelText("b")).toBe(b);
    expect((screen.getByLabelText("b") as HTMLInputElement).value).toBe("saisi");
    expect(screen.getAllByLabelText("a")[0]).toBe(a0);
    rerender(createElement(List, { items: ["z", "a", "a"] })); // retrait d'une valeur au milieu
    expect(screen.getAllByLabelText("a")[0]).toBe(a0);
    rerender(createElement(List, { items: ["y", "a", "a"] })); // une valeur change : seul cet élément est remplacé
    expect(screen.getAllByLabelText("a")[0]).toBe(a0);
    expect(error).not.toHaveBeenCalled(); // pas d'alerte de clés dupliquées
    error.mockRestore();
  });
});
