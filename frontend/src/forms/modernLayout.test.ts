import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const CSS = readFileSync(resolve(process.cwd(), "src", "forms", "forms.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

/** Les règles « selecteur { déclarations } » de premier niveau (les @container et @media imbriqués sont lus à part). */
function rules(css: string): { selector: string; body: string }[] {
  return [...css.matchAll(/([^{}@][^{}]*)\{([^{}]*)\}/g)].map((m) => ({ selector: (m[1] ?? "").trim(), body: m[2] ?? "" }));
}

describe("Formulaire en affichage moderne : la disposition des contrôles est respectée", () => {
  it("aucune règle du mode moderne ne déplace les cellules de la grille (colonne, ligne)", () => {
    const moves = rules(CSS).filter(
      (r) => r.selector.includes(".fb-v-modern") && /\.fb-(cell|tall)\b/.test(r.selector) && /grid-(column|row)\s*:/.test(r.body),
    );
    expect(moves.map((r) => r.selector)).toEqual([]);
  });

  it("la grille garde sa position par cellule (variables de colonne, ligne et largeur)", () => {
    const cell = rules(CSS).find((r) => r.selector === ".fb-cell" && r.body.includes("var(--c)"));
    expect(cell?.body).toContain("span var(--s)");
  });

  it("seul un conteneur étroit replie les contrôles sur une colonne", () => {
    expect(CSS).toMatch(/@container \(max-width: 560px\)\s*\{\s*\.fb-cell\s*\{\s*grid-column: 1 \/ -1/);
  });
});
