import type { JsonSchema } from "../api";

export function display(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "Oui" : "Non";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

/** Human-readable key/value list (schema titles when available), never raw JSON as the main view. */
export function Fields({ schema, data }: { schema?: JsonSchema; data: Record<string, unknown> }) {
  const entries = Object.entries(data);
  if (entries.length === 0) return <p className="hint">Aucune donnée.</p>;
  return (
    <dl className="facts">
      {entries.map(([k, v]) => (
        <div key={k} className="fact">
          <dt>{schema?.properties?.[k]?.title ?? k}</dt>
          <dd>{display(v)}</dd>
        </div>
      ))}
    </dl>
  );
}
