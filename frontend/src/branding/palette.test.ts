import { describe, expect, it } from "vitest";
import {
  contrast,
  DEFAULT_ACCENT,
  isHex,
  MIN_CONTRAST,
  mix,
  NAV_PRESETS,
  navTheme,
  navVars,
  nearestAccessible,
  PRESETS,
  readableOn,
  report,
  soft,
  themeVars,
} from "./palette";

describe("palette de marque", () => {
  it("reprend les valeurs de référence du serveur (tests/test_branding.py)", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#ffffff", "#ffffff")).toBeCloseTo(1, 5);
    expect(contrast(DEFAULT_ACCENT, "#ffffff")).toBeCloseTo(5.36, 2);
    expect(soft(DEFAULT_ACCENT)).toBe("#e2eef2");
    expect(mix("#000000", "#ffffff", 0.5)).toBe("#808080");
    for (const [c, worst] of [
      ["#0e7490", 4.53],
      ["#0b5ed7", 4.9],
      ["#6d28d9", 5.82],
      ["#b91c1c", 5.29],
      ["#15803d", 4.27],
      ["#38bdf8", 1.95],
    ] as const) {
      expect(report(c).worst).toBeCloseTo(worst, 1);
    }
  });
  it("accepte la couleur d'origine et toutes les palettes proposées, refuse les teintes trop claires", () => {
    expect(report(DEFAULT_ACCENT).ok).toBe(true);
    for (const p of PRESETS) expect(report(p.accent).ok, p.name).toBe(true);
    expect(report("#38bdf8").ok).toBe(false);
    expect(report("#ffff00").ok).toBe(false);
  });
  it("trouve la teinte accessible la plus proche (même couleur, plus sombre)", () => {
    const fixed = nearestAccessible("#38bdf8");
    expect(report(fixed).ok).toBe(true);
    expect(fixed).not.toBe("#38bdf8");
    expect(nearestAccessible("#0b5ed7")).toBe("#0b5ed7"); // déjà conforme : inchangée
    expect(nearestAccessible("pas une couleur")).toBe(DEFAULT_ACCENT);
  });
  it("ne pose aucune variable pour la couleur d'origine, et les six rôles pour une autre", () => {
    expect(themeVars(DEFAULT_ACCENT)).toEqual({});
    expect(themeVars("rouge")).toEqual({});
    const v = themeVars("#0B5ED7");
    expect(Object.keys(v).sort()).toEqual(["--accent", "--accent-fg", "--accent-soft", "--focus", "--link", "--primary"]);
    expect(v["--accent"]).toBe("#0b5ed7");
    expect(v["--accent-fg"]).toBe("#ffffff");
  });
});

describe("couleurs des barres", () => {
  it("choisit un texte lisible sur n'importe quel fond (grille de 4 096 couleurs)", () => {
    const steps = Array.from({ length: 16 }, (_, i) => i * 17);
    let worst = 99;
    for (const r of steps)
      for (const g of steps)
        for (const b of steps) {
          const bg = `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
          const t = navTheme(bg);
          const pairs = [
            contrast(t.fg, t.bg),
            contrast(t.muted, t.bg),
            contrast(t.fg, t.hover),
            contrast(t.fg, t.active),
            contrast(t.muted, t.hover),
            contrast(t.muted, t.active),
          ];
          worst = Math.min(worst, ...pairs);
          for (const c of pairs) expect(c, `${bg} ${JSON.stringify(t)}`).toBeGreaterThanOrEqual(MIN_CONTRAST);
        }
    expect(worst).toBeGreaterThanOrEqual(MIN_CONTRAST);
  });
  it("blanc sur sombre, encre sur clair, noir sur les gris moyens", () => {
    expect(navTheme("#0f172a").fg).toBe("#ffffff");
    expect(navTheme("#0f172a").dark).toBe(true);
    expect(navTheme("#ffffff").fg).toBe("#0f172a");
    expect(navTheme("#f1f5f9").dark).toBe(false);
    expect(readableOn("#7a7a7a")).toBe("#000000");
  });
  it("distingue le fond, le survol et l'élément actif", () => {
    for (const bg of ["#ffffff", "#0f172a", "#334155", "#f1f5f9"]) {
      const t = navTheme(bg);
      expect(new Set([t.bg, t.hover, t.active]).size, bg).toBe(3);
    }
  });
  it("pose les variables de la barre voulue, avec une flèche de liste de la couleur du texte", () => {
    const v = navVars("tb", "#0f172a");
    expect(Object.keys(v).sort()).toEqual(["--tb-active", "--tb-bg", "--tb-border", "--tb-chevron", "--tb-fg", "--tb-hover", "--tb-muted"]);
    expect(v["--tb-bg"]).toBe("#0f172a");
    expect(v["--tb-chevron"]).toContain("stroke='%23ffffff'");
    expect(navVars("sb", "n'importe quoi")["--sb-bg"]).toBe("#ffffff");
  });
  it("propose des fonds de barre valides", () => {
    for (const p of NAV_PRESETS) expect(isHex(p.bg), p.name).toBe(true);
  });
});
