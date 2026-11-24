import { describe, expect, it } from "vitest";
import { contrast, mix, PAGE_BG } from "../branding/palette";
import { NOTE_COLOR, NOTE_MAX_SIDE, NOTE_MIN_H, NOTE_MIN_OPACITY, NOTE_MIN_W, NOTE_SWATCHES, noteColor, noteOpacity, noteSide, noteStyle } from "./noteStyle";

describe("style d'une note", () => {
  it("la couleur par défaut est le jaune d'origine ; seule une couleur #rrggbb valide est reprise", () => {
    expect(noteColor(undefined)).toBe(NOTE_COLOR);
    expect(noteColor("#BFDBFE")).toBe("#bfdbfe");
    for (const bad of ["red", "#fff", "#12345g", "url(javascript:alert(1))", "#ffffff; background:url(x)", 12, null, {}])
      expect(noteColor(bad)).toBe(NOTE_COLOR);
  });

  it("l'opacité est bornée entre 10 % et 100 % ; absente ou invalide, la note est opaque", () => {
    expect(noteOpacity(0.5)).toBe(0.5);
    expect(noteOpacity(0)).toBe(NOTE_MIN_OPACITY);
    expect(noteOpacity(7)).toBe(1);
    expect(noteOpacity(0.456)).toBe(0.46);
    for (const bad of [undefined, null, "0.5", Number.NaN, Number.POSITIVE_INFINITY]) expect(noteOpacity(bad)).toBe(1);
  });

  it("une dimension enregistrée est un entier borné, ou rien", () => {
    expect(noteSide(250.4, NOTE_MIN_W)).toBe(250);
    expect(noteSide(5, NOTE_MIN_W)).toBe(NOTE_MIN_W);
    expect(noteSide(99999, NOTE_MIN_H)).toBe(NOTE_MAX_SIDE);
    for (const bad of [undefined, null, "200", Number.NaN]) expect(noteSide(bad, NOTE_MIN_W)).toBeUndefined();
  });

  it("le fond reprend la couleur et l'opacité, la bordure est plus foncée", () => {
    const s = noteStyle("#bfdbfe", 0.5);
    expect(s.background).toBe("rgba(191, 219, 254, 0.5)");
    expect(s.border).toMatch(/^rgba\(\d+, \d+, \d+, 0\.5\)$/);
    expect(noteStyle("#bfdbfe", 0.1).border).toContain("0.45)"); // la bordure reste visible même sur un fond presque transparent
  });

  it("le texte reste lisible (4,5:1) sur le fond tel qu'on le voit, pour toute couleur et toute opacité", () => {
    const hexes = ["#000000", "#ffffff", "#0e7490", "#7f1d1d", "#fff3a8", "#bbf7d0", "#808080", "#1e3a8a", ...NOTE_SWATCHES.map((x) => x.value)];
    for (const c of hexes) {
      for (const o of [0.1, 0.25, 0.5, 0.75, 1]) {
        const seen = mix(PAGE_BG, c, o);
        expect(contrast(noteStyle(c, o).text, seen), `${c} à ${o}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it("propose sept couleurs nommées, toutes valides, la première étant la couleur d'origine", () => {
    expect(NOTE_SWATCHES).toHaveLength(7);
    expect(NOTE_SWATCHES[0]?.value).toBe(NOTE_COLOR);
    for (const sw of NOTE_SWATCHES) expect(noteColor(sw.value)).toBe(sw.value);
  });
});
