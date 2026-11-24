/** Dates du nœud « Programmer » : l'heure saisie est celle du fuseau du nœud (comme au serveur : workflows/schedule_spec.py). */

export const DEFAULT_TZ = "Europe/Paris";
const LOCAL_RE = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?$/;

export const isExpression = (v: string): boolean => v.includes("{{");

/** Décalage (en minutes) du fuseau `tz` à l'instant `utcMs`. */
function offsetMinutes(utcMs: number, tz: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(new Date(utcMs));
  const n = (t: string): number => Number(parts.find((p) => p.type === t)?.value ?? 0);
  const asUtc = Date.UTC(n("year"), n("month") - 1, n("day"), n("hour"), n("minute"), n("second"));
  return Math.round((asUtc - Math.floor(utcMs / 1000) * 1000) / 60000);
}

export function validZone(tz: string): boolean {
  try {
    new Intl.DateTimeFormat("fr-FR", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

/** L'instant (UTC, en ms) d'une heure « murale » `2026-12-01T08:30` dans le fuseau `tz` ; `null` si le texte n'est pas une date et une heure. */
export function zonedToUtc(local: string, tz: string): number | null {
  const m = LOCAL_RE.exec(local.trim());
  if (!m || !validZone(tz)) return null;
  const [y, mo, d, h, mi, s] = [Number(m[1]), Number(m[2]), Number(m[3]), Number(m[4]), Number(m[5]), Number(m[6] ?? 0)];
  const wall = Date.UTC(y, mo - 1, d, h, mi, s);
  let guess = wall - offsetMinutes(wall, tz) * 60000;
  guess = wall - offsetMinutes(guess, tz) * 60000; // second passage : l'offset peut changer autour d'un passage heure d'été / d'hiver
  return Number.isNaN(guess) ? null : guess;
}

/** « dans 3 jours », « dans 2 h 15 min », « dans moins d'une minute » ; « déjà passée » si l'instant est derrière nous. */
export function relative(targetMs: number, nowMs: number): string {
  const s = Math.round((targetMs - nowMs) / 1000);
  if (s <= 0) return "déjà passée";
  if (s < 60) return "dans moins d'une minute";
  const min = Math.floor(s / 60);
  if (min < 60) return `dans ${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `dans ${h} h${min % 60 ? ` ${min % 60} min` : ""}`;
  const days = Math.floor(h / 24);
  return `dans ${days} jour${days > 1 ? "s" : ""}${h % 24 ? ` ${h % 24} h` : ""}`;
}

const UNITS: [string, number][] = [
  ["jours", 86400],
  ["heures", 3600],
  ["minutes", 60],
  ["secondes", 1],
];
export const DELAY_UNITS = UNITS.map(([label, seconds]) => ({ label, seconds }));
/** La plus grande unité qui tombe juste : 7200 → 2 heures. */
export function splitDelay(seconds: number | undefined): { value: number | ""; unit: number } {
  if (!seconds || seconds < 1) return { value: "", unit: 3600 };
  const unit = UNITS.find(([, u]) => seconds % u === 0)?.[1] ?? 1;
  return { value: seconds / unit, unit };
}

/** Demain 08:00, heure locale du navigateur : la valeur proposée à la création d'une étape (valide tout de suite). */
export function tomorrowAt8(now = new Date()): string {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 8, 0);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T08:00`;
}

export function zoneNames(): string[] {
  const all = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.("timeZone");
  return all && all.length > 0 ? all : [DEFAULT_TZ, "UTC", "Europe/London", "America/New_York", "America/Los_Angeles", "Asia/Tokyo"];
}
