import { fr, type MessageKey } from "./fr";

const catalogs: Record<string, Record<string, string>> = { fr };
const locale = "fr";

export function t(key: MessageKey): string {
  return catalogs[locale]?.[key] ?? key;
}

/** « il y a 3 jours », « hier »… (the exact date goes in the title attribute next to it) */
export function relativeDate(iso: string | null, now = Date.now()): string {
  if (!iso) return "—";
  const diff = new Date(iso).getTime() - now;
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  const units: [Intl.RelativeTimeFormatUnit, number][] = [["year", 31536e6], ["month", 2592e6], ["week", 6048e5], ["day", 864e5], ["hour", 36e5], ["minute", 6e4]];
  for (const [u, ms] of units) if (Math.abs(diff) >= ms) return rtf.format(Math.round(diff / ms), u);
  return rtf.format(0, "minute");
}

export function formatDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString(locale, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"; // « 3 nov. 2026, 16:16 »
}
