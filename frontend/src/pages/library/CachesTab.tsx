import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type ApiError } from "../../api";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { errorText, useWsMutation } from "../../lib";
import { Badge, Confirm, EmptyRow, SearchBox, Section, usePaged } from "../../components/AdminUi";
import { Overlay } from "../../components/Overlay";

export interface CacheRow {
  id: number;
  key: string;
  environment: string;
  version: number;
  size_bytes: number;
  expires_at: string | null;
  expired: boolean;
  sensitive: boolean;
  written_by_workflow: string;
  written_by_run: string | null;
  updated_at: string;
}
interface CacheDetail extends CacheRow {
  value: unknown;
  masked: boolean;
}

const ENV: Record<string, string> = { dev: "Développement", test: "Test", prod: "Production" };
const size = (n: number): string => (n < 1024 ? `${n} o` : n < 1048576 ? `${(n / 1024).toFixed(1)} Ko` : `${(n / 1048576).toFixed(1)} Mo`);

function ValueDialog({ row, onClose }: { row: CacheRow; onClose: () => void }) {
  const { workspace } = useApp();
  const q = useQuery<CacheDetail, ApiError>({
    queryKey: ["cache-value", workspace.slug, row.id],
    queryFn: () => api<CacheDetail>(`/workspaces/${workspace.slug}/caches/${row.id}/`),
  });
  return (
    <Overlay
      title={`Cache « ${row.key} »`}
      onClose={onClose}
      wide
      description={`${ENV[row.environment] ?? row.environment}, version ${row.version}, ${size(row.size_bytes)}`}
    >
      {q.isLoading && <p role="status">Chargement…</p>}
      {q.error && (
        <p role="alert" className="field-error">
          Erreur : {errorText(q.error)}
        </p>
      )}
      {q.data?.masked && (
        <p role="status" className="hint">
          Valeur sensible : seuls les administrateurs de l'espace peuvent la voir. Les workflows de l'espace peuvent toujours la lire.
        </p>
      )}
      {q.data && !q.data.masked && (
        <section aria-label="Valeur du cache">
          <pre className="code-block">{JSON.stringify(q.data.value, null, 2)}</pre>
        </section>
      )}
    </Overlay>
  );
}

/** Le cache nommé de l'espace (ADR 0032) : ce que les workflows y ont écrit, avec l'âge, la taille et l'origine, et de quoi l'invalider à la main. */
export function CachesTab({ exactKey }: { exactKey?: string } = {}) {
  const { can, workspace } = useApp();
  const allowed = can("edit");
  const list = useQuery<{ count: number; results: CacheRow[] }, ApiError>({
    queryKey: ["caches", workspace.slug, exactKey ?? ""],
    queryFn: () => api(`/workspaces/${workspace.slug}/caches/${exactKey ? `?key=${encodeURIComponent(exactKey)}` : ""}`),
    enabled: allowed,
  });
  const del = useWsMutation<{ id: number }>("DELETE", (i) => `/caches/${i.id}/`, ["caches"]);
  const [search, setSearch] = useState("");
  const [env, setEnv] = useState("");
  const [viewing, setViewing] = useState<CacheRow | null>(null);
  const [toDel, setToDel] = useState<CacheRow | null>(null);
  const all = list.data?.results ?? [];
  const rows = all.filter((r) => (!env || r.environment === env) && (!search.trim() || r.key.toLowerCase().includes(search.trim().toLowerCase())));
  const paged = usePaged(rows, 10);
  if (!allowed) return <p className="hint">Le cache est réservé aux personnes qui conçoivent des workflows.</p>;
  return (
    <>
      <Section
        title={exactKey ? `Cache de ce workflow : ${exactKey}` : "Cache de l'espace"}
        hint={
          exactKey
            ? "Ce que ce workflow publie sous sa clé, dans chaque environnement. Les autres workflows le relisent avec l'étape « Cache ». N'y mettez jamais de secret."
            : "Valeurs que les workflows s'échangent : écrites par une étape « Cache » ou par le réglage « Publier le résultat dans le cache » d'un workflow. Séparé par environnement. N'y mettez jamais de secret."
        }
      >
        <div className="adm-filters">
          {!exactKey && <SearchBox label="Rechercher une clé" value={search} onChange={setSearch} placeholder="Clé" />}
          <label htmlFor="cache-env" className="sr-only">
            Environnement
          </label>
          <select id="cache-env" value={env} onChange={(e) => setEnv(e.target.value)}>
            <option value="">Tous les environnements</option>
            {Object.entries(ENV).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
          <span className="lst-count" role="status">
            {rows.length} entrée(s)
          </span>
        </div>
        <div className="table-wrap">
          <table className="adm-table">
            <caption className="sr-only">Entrées du cache</caption>
            <thead>
              <tr>
                <th scope="col">Clé</th>
                <th scope="col">Environnement</th>
                <th scope="col">Version</th>
                <th scope="col">Taille</th>
                <th scope="col">Écrit le</th>
                <th scope="col">Expire</th>
                <th scope="col">Écrit par</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {paged.slice.map((r) => (
                <tr key={r.id}>
                  <td>
                    <strong className="mono">{r.key}</strong>
                    {r.sensitive && <Badge tone="warn">Sensible</Badge>}
                  </td>
                  <td>
                    <Badge tone={r.environment === "prod" ? "warn" : "muted"}>{ENV[r.environment] ?? r.environment}</Badge>
                  </td>
                  <td>{r.version}</td>
                  <td>{size(r.size_bytes)}</td>
                  <td>{formatDate(r.updated_at)}</td>
                  <td>{r.expires_at ? r.expired ? <Badge tone="bad">Expiré</Badge> : formatDate(r.expires_at) : "Jamais"}</td>
                  <td>{r.written_by_workflow || "—"}</td>
                  <td className="row-actions">
                    <button type="button" onClick={() => setViewing(r)} aria-label={`Voir la valeur de ${r.key}`}>
                      Voir
                    </button>
                    <button
                      type="button"
                      className="danger"
                      onClick={() => setToDel(r)}
                      aria-label={`Invalider ${r.key} (${ENV[r.environment] ?? r.environment})`}
                    >
                      Invalider
                    </button>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <EmptyRow cols={8}>
                  {list.isLoading
                    ? "Chargement…"
                    : all.length === 0
                      ? exactKey
                        ? "Rien dans le cache pour l'instant : la valeur apparaît à la fin de la prochaine exécution réussie de ce workflow."
                        : "Aucune entrée : un workflow en écrit une avec une étape « Cache » ou le réglage « Publier le résultat dans le cache »."
                      : "Aucune entrée ne correspond."}
                </EmptyRow>
              )}
            </tbody>
          </table>
        </div>
        {paged.pager("Pagination du cache")}
        {list.data && list.data.count > all.length && (
          <p className="hint">
            Seules les {all.length} premières entrées sont affichées ({list.data.count} au total) : affinez la recherche.
          </p>
        )}
        {list.error && (
          <p role="alert" className="field-error">
            Erreur : {errorText(list.error)}
          </p>
        )}
      </Section>
      {viewing && <ValueDialog row={viewing} onClose={() => setViewing(null)} />}
      {toDel && (
        <Confirm
          title="Invalider cette entrée ?"
          confirmLabel="Invalider"
          busy={del.isPending}
          onCancel={() => setToDel(null)}
          onConfirm={() => {
            del.mutate({ id: toDel.id });
            setToDel(null);
          }}
        >
          <p>
            La valeur <strong className="mono">{toDel.key}</strong> ({ENV[toDel.environment] ?? toDel.environment}) sera supprimée. Les workflows qui la lisent
            la trouveront absente jusqu'à sa prochaine écriture.
          </p>
        </Confirm>
      )}
      {del.error && (
        <p role="alert" className="field-error">
          Erreur : {errorText(del.error)}
        </p>
      )}
    </>
  );
}
