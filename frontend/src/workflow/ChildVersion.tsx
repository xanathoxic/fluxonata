import type { Page, WorkflowSummary, WorkflowVersionInfo } from "../api";
import { useWsQuery } from "../lib";
import type { GraphNode } from "./types";

/** Valeur de `version` : « toujours la dernière version publiée », résolue à chaque exécution (jamais figée à la publication). */
export const LATEST = "latest";

/** Les versions publiées du workflow appelé par une étape `subworkflow`, `foreach` ou `while` (la plus récente en premier). */
export function useChildVersions(slug: string | undefined) {
  const q = useWsQuery<Page<WorkflowVersionInfo>>(`workflow-versions:${slug ?? ""}`, `/workflows/${slug ?? "-"}/versions/`, { enabled: !!slug });
  return { versions: q.data?.results ?? [], loading: !!slug && q.isLoading, failed: q.isError };
}

/**
 * Version du workflow appelé. La valeur affichée est TOUJOURS celle du graphe (`node.version`) : jamais la dernière version du catalogue.
 * `latest` = toujours la dernière version publiée, résolue à chaque exécution : une nouvelle publication de l'enfant s'applique sans republier ce workflow.
 * Vide = la dernière version AU MOMENT de la publication de ce workflow, figée sur ce numéro (comportement historique, conservé tel quel).
 * Aucune étape existante n'est modifiée tant que la personne ne choisit pas explicitement une version.
 */
export function ChildVersionField({
  node,
  child,
  editable,
  onUpdate,
}: {
  node: GraphNode;
  child: WorkflowSummary | undefined;
  editable: boolean;
  onUpdate: (patch: Partial<GraphNode>) => void;
}) {
  const { versions, loading, failed } = useChildVersions(node.workflow);
  if (!node.workflow) return null;
  const latest = versions[0]?.version ?? child?.latest_version?.version ?? "";
  const pinned = node.version ?? "";
  const known = pinned === "" || pinned === LATEST || versions.some((v) => v.version === pinned);
  const newer = pinned !== "" && pinned !== LATEST && latest !== "" && latest !== pinned && versions.some((v) => v.version === pinned);
  return (
    <div className="field">
      <label htmlFor="sub-ver">Version du workflow appelé</label>
      <select id="sub-ver" value={pinned} disabled={!editable} onChange={(e) => onUpdate({ version: e.target.value || undefined })}>
        <option value={LATEST}>{latest ? `Toujours la dernière version publiée (actuellement ${latest})` : "Toujours la dernière version publiée"}</option>
        <option value="">{latest ? `La dernière à la publication (actuellement ${latest}), puis figée` : "La dernière à la publication, puis figée"}</option>
        {versions.map((v) => (
          <option key={v.version} value={v.version}>
            {v.version}
            {v.version === latest ? " (dernière publiée)" : ""}
          </option>
        ))}
        {!known && <option value={pinned}>{pinned} (introuvable parmi les versions publiées)</option>}
      </select>
      {loading && (
        <p className="hint" role="status">
          Chargement des versions…
        </p>
      )}
      {failed && (
        <p className="field-error" role="alert">
          Les versions de ce workflow n'ont pas pu être chargées : la version affichée est celle du brouillon.
        </p>
      )}
      {pinned !== "" && !known && !loading && !failed && (
        <p className="field-error" role="alert">
          La version {pinned} n'existe pas : choisissez une version publiée.
        </p>
      )}
      {newer && (
        <p className="hint">
          Cette étape appelle la version {pinned}. La version {latest}, plus récente, n'est pas utilisée tant que vous ne la choisissez pas.
        </p>
      )}
      {pinned === "" && (
        <p className="hint">Aucune version n'est épinglée : à la publication, cette étape sera figée sur la dernière version publiée de ce moment.</p>
      )}
      {pinned === LATEST && (
        <p className="hint">
          Chaque exécution appelle la dernière version publiée de ce workflow : une nouvelle publication s'applique tout de suite, sans republier celui-ci. Les
          entrées doivent rester compatibles.
        </p>
      )}
    </div>
  );
}
