import { mix, PAGE_BG, readableOn, rgb } from "../branding/palette";

/** Style d'une note du schéma : sa couleur (n'importe quel #rrggbb) et l'opacité de son fond (10 à 100 %). Le texte, lui, reste toujours opaque et lisible
 *  (4,5:1) quels que soient la couleur et le fond sur lequel la note est posée. Toute valeur enregistrée est revérifiée ici : une valeur inconnue ou
 *  malformée est ignorée, jamais écrite telle quelle dans une feuille de style. */
export const NOTE_COLOR = "#fff3a8";
export const NOTE_SWATCHES: { name: string; value: string }[] = [
  { name: "Jaune", value: NOTE_COLOR },
  { name: "Bleu", value: "#bfdbfe" },
  { name: "Vert", value: "#bbf7d0" },
  { name: "Rose", value: "#fbcfe8" },
  { name: "Orange", value: "#fed7aa" },
  { name: "Gris", value: "#e5e7eb" },
  { name: "Blanc", value: "#ffffff" },
];
export const NOTE_MIN_OPACITY = 0.1;
export const NOTE_MIN_W = 90;
export const NOTE_MIN_H = 44;
export const NOTE_MAX_SIDE = 2000;

const HEX6 = /^#[0-9a-fA-F]{6}$/;

export const noteColor = (v: unknown): string => (typeof v === "string" && HEX6.test(v) ? v.toLowerCase() : NOTE_COLOR);

export function noteOpacity(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? Math.min(1, Math.max(NOTE_MIN_OPACITY, Math.round(v * 100) / 100)) : 1;
}

/** Une largeur ou hauteur enregistrée : un entier entre le minimum et `NOTE_MAX_SIDE`, ou rien (taille automatique). */
export function noteSide(v: unknown, min: number): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? Math.min(NOTE_MAX_SIDE, Math.max(min, Math.round(v))) : undefined;
}

export interface NoteStyle {
  background: string;
  border: string;
  text: string;
}

export function noteStyle(color?: unknown, opacity?: unknown): NoteStyle {
  const c = noteColor(color);
  const o = noteOpacity(opacity);
  const [r, g, b] = rgb(c);
  const [er, eg, eb] = rgb(mix(c, "#000000", 0.28));
  return {
    background: `rgba(${r}, ${g}, ${b}, ${o})`,
    border: `rgba(${er}, ${eg}, ${eb}, ${Math.max(o, 0.45)})`,
    text: readableOn(mix(PAGE_BG, c, o)), // le fond tel qu'on le voit : la note mélangée au fond du schéma
  };
}
