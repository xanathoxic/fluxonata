import { describe, expect, it } from "vitest";
import { floorFontShorthand, floorFontSize, scaleLengths } from "./postcss-scale.mjs";

describe("mise à l'échelle des longueurs en pixels (postcss-scale.mjs)", () => {
  it("multiplie chaque longueur de plus de 1 px par le facteur d'échelle", () => {
    expect(scaleLengths("8px 16px")).toBe("calc(8px * var(--ui-s)) calc(16px * var(--ui-s))");
    expect(scaleLengths("calc(100vh - 60px)")).toBe("calc(100vh - calc(60px * var(--ui-s)))");
    expect(scaleLengths("minmax(310px, 1fr)")).toBe("minmax(calc(310px * var(--ui-s)), 1fr)");
  });

  it("ne touche ni les filets de 1 px, ni les chaînes, ni les url()", () => {
    expect(scaleLengths("1px solid var(--border)")).toBe("1px solid var(--border)");
    expect(scaleLengths('url("data:image/svg+xml,width=\'24px\'") 10px')).toBe('url("data:image/svg+xml,width=\'24px\'") calc(10px * var(--ui-s))');
    expect(scaleLengths('"12px"')).toBe('"12px"');
  });

  it("n'emploie jamais `--s` : la grille des formulaires l'utilise déjà (nombre de colonnes d'un champ) et le masquerait dans un formulaire", () => {
    const out = [scaleLengths("12px"), floorFontSize("13px"), floorFontShorthand("13px/1.5 monospace")].join(" ");
    expect(out).toContain("var(--ui-s)");
    expect(out).not.toMatch(/var\(--s\)/);
  });
});

describe("plancher de taille de texte", () => {
  it("borne la taille à 11 px, y compris en rem, em et jetons", () => {
    expect(floorFontSize("13px")).toBe("max(11px, calc(13px * var(--ui-s)))");
    expect(floorFontSize(".82rem")).toBe("max(11px, .82rem)");
    expect(floorFontSize("var(--fs-sm)")).toBe("max(11px, var(--fs-sm))");
  });

  it("laisse intacts 0 et les mots-clés", () => {
    for (const v of ["0", "inherit", "small", "initial"]) expect(floorFontSize(v)).toBe(v);
  });

  it("borne la taille du raccourci `font`, sans toucher la famille", () => {
    expect(floorFontShorthand("600 .82rem ui-monospace, monospace")).toBe("600 max(11px, .82rem) ui-monospace, monospace");
    expect(floorFontShorthand("inherit")).toBe("inherit");
  });
});
