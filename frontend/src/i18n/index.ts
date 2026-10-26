import { fr, type MessageKey } from "./fr";

const catalogs: Record<string, Record<string, string>> = { fr };
const locale = "fr";

export function t(key: MessageKey): string {
  return catalogs[locale]?.[key] ?? key;
}

export function formatDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString(locale, { dateStyle: "short", timeStyle: "medium" }) : "—";
}
