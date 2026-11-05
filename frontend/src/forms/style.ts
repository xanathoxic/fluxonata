import type { CSSProperties } from "react";
import type { FormStyle } from "./types";

export const ACCENTS = ["#0e7490", "#1d4ed8", "#6d28d9", "#be185d", "#b45309", "#15803d", "#334155"];
export const WIDTHS: Record<NonNullable<FormStyle["width"]>, number | null> = { narrow: 480, medium: 720, wide: 960, full: null };
const RADII = { none: "0px", small: "4px", medium: "8px", large: "14px" };

function luminance(hex: string): number {
  const lin = (v: number) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return 0.2126 * lin(r as number) + 0.7152 * lin(g as number) + 0.0722 * lin(b as number);
}
/** Contrast of white text on this colour: buttons are white on the accent (same formula as the server's check). */
export const contrastWithWhite = (hex: string): number => 1.05 / (luminance(hex) + 0.05);
export const isHex = (v: string) => /^#[0-9a-fA-F]{6}$/.test(v);
export const MIN_CONTRAST = 4.5;

/** Classes and variables that dress a form. Only closed choices and one validated colour reach the CSS. */
export function styleProps(style: FormStyle | undefined): { className: string; style: CSSProperties } {
  const s = style ?? {};
  const vars: Record<string, string> = {};
  if (s.accent && isHex(s.accent) && contrastWithWhite(s.accent) >= MIN_CONTRAST) {
    vars["--accent"] = s.accent;
    vars["--accent-fg"] = "#ffffff";
    vars["--accent-soft"] = `color-mix(in srgb, ${s.accent} 14%, transparent)`;
  }
  if (s.radius) vars["--fb-radius"] = RADII[s.radius] ?? RADII.medium;
  const w = s.width ? WIDTHS[s.width] : undefined;
  if (w) vars["maxWidth"] = `${w}px`;
  const { maxWidth, ...cssVars } = vars;
  return {
    className: ["fb-form", `fb-d-${s.density ?? "normal"}`, `fb-l-${s.labels ?? "top"}`, `fb-s-${s.sections ?? "card"}`, `fb-b-${s.button ?? "left"}`].join(" "),
    style: { ...(cssVars as CSSProperties), ...(maxWidth ? { maxWidth } : {}) },
  };
}
