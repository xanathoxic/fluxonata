import type { DataSource } from "./types";

const PLACEHOLDER = /\{([A-Za-z_][A-Za-z0-9_]*)\}/g;
const namesIn = (text: string): string[] => [...text.matchAll(PLACEHOLDER)].map((m) => m[1] ?? "").filter(Boolean);
const isEmpty = (v: unknown): boolean => v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0);

/**
 * The fields a dynamic list still waits for, so that the server is not asked (and no error is shown) before they have a value:
 * the `{champ}` of the path BEFORE its `?` (`/acl/{champ1}/org/{champ2}`: an empty one leaves a hole in the address) and those of the
 * path where the items are read (`Groups.{groupe}`: it leads nowhere without a value).
 * A `{champ}` after the `?` may legitimately be empty (`?marque={marque}` = all brands) and `{q}` is the search box: both are still sent.
 */
export function waitingFor(source: DataSource | undefined, values: Record<string, unknown>): string[] {
  if (!source) return [];
  const declared = new Set(source.params ?? []);
  const route = namesIn((source.path ?? "").split(/[?#]/)[0] ?? "");
  const items = namesIn(source.items_path ?? "");
  const chooser = source.connection_from ? [source.connection_from] : []; // no value, no connection: nothing to ask
  return [...new Set([...route, ...items, ...chooser])].filter((p) => p !== "q" && declared.has(p) && isEmpty(values[p]));
}

/** What a reading calls, for a summary: its connection, or the field that picks one among the allowed ones. */
export const connectionLabel = (source: Pick<DataSource, "connection" | "connection_from">): string =>
  source.connection_from ? `connexion selon « ${source.connection_from} »` : source.connection || "(connexion ?)";

/** The closed list of connections, as the designer types it: one line per choice, « valeur du champ | connexion ». */
export const choicesToText = (choices: Record<string, string> | undefined): string => Object.entries(choices ?? {}).map(([v, c]) => `${v} | ${c}`).join("\n");
export const textToChoices = (text: string): Record<string, string> => {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const at = line.indexOf("|");
    const value = (at < 0 ? "" : line.slice(0, at)).trim();
    const name = (at < 0 ? "" : line.slice(at + 1)).trim();
    if (value && name && Object.keys(out).length < 10) out[value] = name;
  }
  return out;
};

/** The reading with the field that picks the connection among the fields it reads: its value must reach the server. */
export function withChooser<T extends DataSource>(source: T): T {
  const key = source.connection_from?.trim();
  if (!key || (source.params ?? []).includes(key)) return source;
  return { ...source, params: [...(source.params ?? []), key].slice(0, 5) };
}
