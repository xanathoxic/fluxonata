import { useEffect, useState } from "react";
import type { JsonSchema } from "../api";

const TYPES: [string, string][] = [
  ["string", "Texte"],
  ["integer", "Entier"],
  ["number", "Nombre"],
  ["boolean", "Oui / Non"],
  ["array", "Liste"],
  ["object", "Objet"],
];
const IDENT = /[^A-Za-z0-9_]/g;

export interface FieldRow {
  name: string;
  schema: JsonSchema;
  required: boolean;
}

/** Le schéma, s'il est un objet JSON lisible ; `null` sinon (le formulaire laisse alors le texte brut). */
export function parseObjectSchema(text: string): JsonSchema | null {
  try {
    const v: unknown = JSON.parse(text);
    return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as JsonSchema) : null;
  } catch {
    return null;
  }
}

export function fieldRows(schema: JsonSchema): FieldRow[] {
  const required = Array.isArray(schema.required) ? schema.required : [];
  return Object.entries(schema.properties ?? {}).map(([name, s]) => ({ name, schema: s, required: required.includes(name) }));
}

/** Réécrit les champs d'un schéma en gardant tout le reste (description du schéma, `additionalProperties`, mots-clés propres à chaque champ). */
export function withRows(schema: JsonSchema, rows: FieldRow[]): JsonSchema {
  const { required: _r, properties: _p, ...rest } = schema;
  void _r;
  void _p;
  const required = rows.filter((r) => r.required).map((r) => r.name);
  return { ...rest, type: "object", ...(required.length ? { required } : {}), properties: Object.fromEntries(rows.map((r) => [r.name, r.schema])) };
}

export function defaultValue(raw: string, type: unknown): unknown {
  if (raw === "") return undefined;
  if (type === "integer" || type === "number") return Number.isNaN(Number(raw)) ? undefined : Number(raw);
  if (type === "boolean") return raw === "true";
  if (type === "array" || type === "object") {
    try {
      return JSON.parse(raw);
    } catch {
      return undefined;
    }
  }
  return raw;
}

/** L'identifiant se tape librement (on peut tout effacer avant de retaper) ; il n'est enregistré que s'il est non vide et libre, sinon l'ancien est rétabli en quittant le champ. */
function NameInput({ id, value, label, onCommit }: { id: string; value: string; label: string; onCommit: (name: string) => boolean }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  return (
    <>
      <label className="sr-only" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        value={text}
        onChange={(e) => {
          const t = e.target.value.replace(IDENT, "_");
          setText(t);
          onCommit(t);
        }}
        onBlur={() => setText(value)}
      />
    </>
  );
}

export const shown = (v: unknown) => (v === undefined ? "" : typeof v === "string" ? v : JSON.stringify(v));

/** Édition guidée d'un schéma d'entrées ou de sorties : une ligne par champ (identifiant, titre, type, obligatoire, valeur par défaut, description). Le texte JSON reste la source : tout ce que le tableau ne couvre pas (valeurs possibles, bornes, listes imbriquées…) se règle dans « Avancé ». */
export function SchemaFields({ value, onChange, kind, idPrefix }: { value: string; onChange: (text: string) => void; kind: "in" | "out"; idPrefix: string }) {
  const schema = parseObjectSchema(value);
  const label = kind === "in" ? "entrée" : "sortie";
  const write = (s: JsonSchema) => onChange(JSON.stringify(s, null, 2));
  const advanced = (
    <details className="schema-advanced">
      <summary>Avancé : JSON du schéma des {label}s</summary>
      <div className="field">
        <label htmlFor={`${idPrefix}-json`}>Schéma des {label}s (JSON Schema)</label>
        <textarea id={`${idPrefix}-json`} rows={12} className="mono" value={value} onChange={(e) => onChange(e.target.value)} spellCheck={false} />
      </div>
      <p className="hint">
        Valeurs possibles (<code>enum</code>), bornes, format, listes imbriquées : ces réglages se font ici ; le tableau les conserve.
      </p>
    </details>
  );
  if (!schema) {
    return (
      <>
        <p role="alert" className="field-error">
          Le JSON du schéma n'est pas lisible : corrigez-le ci-dessous pour retrouver le tableau.
        </p>
        <div className="field">
          <label htmlFor={`${idPrefix}-json`}>Schéma des {label}s (JSON Schema)</label>
          <textarea id={`${idPrefix}-json`} rows={12} className="mono" value={value} onChange={(e) => onChange(e.target.value)} spellCheck={false} />
        </div>
      </>
    );
  }
  const rows = fieldRows(schema);
  const patch = (i: number, p: Partial<FieldRow> & { schema?: JsonSchema }) =>
    write(
      withRows(
        schema,
        rows.map((r, j) => (j === i ? { ...r, ...p, schema: p.schema ?? r.schema } : r)),
      ),
    );
  const setKey = (i: number, key: string, v: unknown) => {
    const next = { ...(rows[i]?.schema ?? {}) } as Record<string, unknown>;
    if (v === undefined || v === "") delete next[key];
    else next[key] = v;
    patch(i, { schema: next as JsonSchema });
  };
  const rename = (i: number, name: string): boolean => {
    if (!name || rows.some((r, j) => j !== i && r.name === name)) return false; // vide ou déjà pris : on garde l'ancien
    patch(i, { name });
    return true;
  };
  const add = () => {
    let n = rows.length + 1;
    while (rows.some((r) => r.name === `champ_${n}`)) n++;
    write(withRows(schema, [...rows, { name: `champ_${n}`, schema: { type: "string", title: `Champ ${n}`, description: "" }, required: false }]));
  };
  const plural = kind === "in" ? "entrées" : "sorties";
  return (
    <section aria-label={`Champs des ${plural}`} className="schema-fields">
      <div className="field">
        <label htmlFor={`${idPrefix}-desc`}>Description du schéma (obligatoire s'il n'y a aucun champ : « Cette action n'a aucune {label}. »)</label>
        <input id={`${idPrefix}-desc`} value={schema.description ?? ""} onChange={(e) => write({ ...schema, description: e.target.value || undefined })} />
      </div>
      <div className="table-wrap">
        <table className="adm-table">
          <caption className="sr-only">Champs des {plural}</caption>
          <thead>
            <tr>
              <th scope="col">Identifiant</th>
              <th scope="col">Titre</th>
              <th scope="col">Type</th>
              <th scope="col">{kind === "in" ? "Obligatoire" : "Toujours présente"}</th>
              {kind === "in" && <th scope="col">Valeur par défaut</th>}
              <th scope="col">Description</th>
              <th scope="col">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: liste affichée en entier, l'identifiant est modifiable et ne peut donc pas servir de clé
              <tr key={i}>
                <td>
                  <NameInput
                    id={`${idPrefix}-n-${i}`}
                    label={`Identifiant de ${kind === "in" ? "l'entrée" : "la sortie"} ${i + 1}`}
                    value={r.name}
                    onCommit={(name) => rename(i, name)}
                  />
                </td>
                <td>
                  <label className="sr-only" htmlFor={`${idPrefix}-t-${i}`}>
                    Titre de {r.name}
                  </label>
                  <input id={`${idPrefix}-t-${i}`} value={r.schema.title ?? ""} onChange={(e) => setKey(i, "title", e.target.value)} />
                </td>
                <td>
                  <label className="sr-only" htmlFor={`${idPrefix}-y-${i}`}>
                    Type de {r.name}
                  </label>
                  <select id={`${idPrefix}-y-${i}`} value={String(r.schema.type ?? "string")} onChange={(e) => setKey(i, "type", e.target.value)}>
                    {TYPES.map(([k, l]) => (
                      <option key={k} value={k}>
                        {l}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <input type="checkbox" id={`${idPrefix}-r-${i}`} checked={r.required} onChange={(e) => patch(i, { required: e.target.checked })} />
                  <label htmlFor={`${idPrefix}-r-${i}`} className="sr-only">
                    {r.name} : {kind === "in" ? "obligatoire" : "toujours présente"}
                  </label>
                </td>
                {kind === "in" && (
                  <td>
                    <label className="sr-only" htmlFor={`${idPrefix}-d-${i}`}>
                      Valeur par défaut de {r.name}
                    </label>
                    <input
                      id={`${idPrefix}-d-${i}`}
                      defaultValue={shown(r.schema.default)}
                      // biome-ignore lint/suspicious/noArrayIndexKey: la clé ne sert qu'à relire la valeur par défaut quand le type change
                      key={`${idPrefix}-d-${i}-${String(r.schema.type)}`}
                      onBlur={(e) => setKey(i, "default", defaultValue(e.target.value, r.schema.type))}
                    />
                  </td>
                )}
                <td>
                  <label className="sr-only" htmlFor={`${idPrefix}-e-${i}`}>
                    Description de {r.name}
                  </label>
                  <input id={`${idPrefix}-e-${i}`} value={r.schema.description ?? ""} onChange={(e) => setKey(i, "description", e.target.value)} />
                </td>
                <td>
                  <button
                    type="button"
                    onClick={() =>
                      write(
                        withRows(
                          schema,
                          rows.filter((_, k) => k !== i),
                        ),
                      )
                    }
                    aria-label={`Supprimer ${kind === "in" ? "l'entrée" : "la sortie"} ${r.name}`}
                  >
                    Supprimer
                  </button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={kind === "in" ? 7 : 6} className="hint">
                  Aucun champ : précisez-le dans la description du schéma.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <button type="button" onClick={add}>
        Ajouter {kind === "in" ? "une entrée" : "une sortie"}
      </button>
      {advanced}
    </section>
  );
}
