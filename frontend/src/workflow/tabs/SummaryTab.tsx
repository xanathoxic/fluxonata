import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { Page, WorkflowRun } from "../../api";
import { StatusBadge } from "../../components/StatusBadge";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { errorText, useWsMutation, useWsQuery } from "../../lib";
import { useDraft } from "../DraftContext";

export function SummaryTab() {
  const d = useDraft();
  const { can } = useApp();
  const meta = useWsMutation<{ description: string; folder: string }>("PATCH", `/workflows/${d.slug}/meta/`, ["workflows"]);
  const runs = useWsQuery<Page<WorkflowRun>>(`wf-runs-${d.slug}`, `/workflow-runs/?workflow=${d.slug}`);
  const [description, setDescription] = useState(d.wf.description);
  const [folder, setFolder] = useState(d.wf.folder);
  useEffect(() => { setDescription(d.wf.description); setFolder(d.wf.folder); }, [d.wf.description, d.wf.folder]);
  const editable = can("edit");
  const nodes = d.graph.nodes.length;
  const v = d.wf.latest_version;
  return (
    <section aria-label="Résumé du workflow">
      <h2>Résumé</h2>
      <dl className="facts">
        <dt>Dernière version publiée</dt><dd>{v ? `${v.version} (${formatDate(v.created_at)})` : "Jamais publié"}</dd>
        <dt>Propriétaire</dt><dd>{d.wf.owner ?? "—"}</dd>
        <dt>Contenu du brouillon</dt><dd>{nodes} étape(s), {Object.keys(d.graph.inputs.properties ?? {}).length} entrée(s), {(d.graph.variables ?? []).length} variable(s)</dd>
        <dt>Dernière modification</dt><dd>{formatDate(d.wf.updated_at)}</dd>
        {d.wf.deprecated && <><dt>Statut</dt><dd>⚠ Déprécié : {d.wf.deprecation_reason || "aucun motif"}</dd></>}
      </dl>
      <form onSubmit={(e) => { e.preventDefault(); meta.mutate({ description, folder }, { onSuccess: () => void d.refetchWorkflow() }); }}>
        <fieldset disabled={!editable} className="bare">
          <div className="field"><label htmlFor="sum-desc">Description</label><textarea id="sum-desc" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} /></div>
          <div className="field"><label htmlFor="sum-folder">Dossier</label><input id="sum-folder" value={folder} placeholder="ex. Réseau/DNS" onChange={(e) => setFolder(e.target.value)} /><p className="hint">Les dossiers organisent l'arbre des workflows (séparez les niveaux par « / »).</p></div>
          <button type="submit" disabled={meta.isPending}>Enregistrer</button>
          {meta.isSuccess && <p role="status">Enregistré.</p>}
          {meta.error && <p role="alert" className="field-error">Erreur : {errorText(meta.error)}</p>}
        </fieldset>
      </form>
      <h3>Dernières exécutions</h3>
      <ul className="plain">
        {runs.data?.results.slice(0, 5).map((r) => <li key={r.id}><StatusBadge status={r.status} label={r.status_label} /> <Link to={`/runs/${r.id}`}>{formatDate(r.created_at)}</Link> <span className="hint">v{r.workflow.version} · {r.environment}</span></li>)}
        {runs.data?.results.length === 0 && <li className="hint">Aucune exécution.</li>}
      </ul>
    </section>
  );
}
