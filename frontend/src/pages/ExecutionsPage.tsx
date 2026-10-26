import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { apiAll, type Execution, type WorkflowRun } from "../api";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import { useApp } from "../context";
import { formatDate, t } from "../i18n";

interface Row { key: string; to: string; name: string; kind: string; status: string; label: string; env: string; at: string }

export function ExecutionsPage() {
  const { workspace } = useApp();
  const actions = useQuery({ queryKey: ["executions", workspace.slug], queryFn: () => apiAll<Execution>(`/workspaces/${workspace.slug}/executions/`, 500), refetchInterval: 3000 });
  const flows = useQuery({ queryKey: ["wruns", workspace.slug], queryFn: () => apiAll<WorkflowRun>(`/workspaces/${workspace.slug}/workflow-runs/`, 500), refetchInterval: 3000 });
  const rows: Row[] = [
    ...(actions.data?.results ?? []).map((e) => ({ key: e.id, to: `/executions/${e.id}`, name: e.action.name, kind: "Action", status: e.status, label: e.status_label, env: e.environment, at: e.created_at })),
    ...(flows.data?.results ?? []).map((r) => ({ key: r.id, to: `/runs/${r.id}`, name: r.workflow.name, kind: "Workflow", status: r.status, label: r.needs_decision ? "Décision requise" : r.status_label, env: r.environment, at: r.created_at })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [limit, setLimit] = useState(50);
  const shownAll = rows.filter((r) => (!q || r.name.toLowerCase().includes(q.toLowerCase())) && (!status || r.status === status) && (!kind || r.kind === kind));
  const shown = shownAll.slice(0, limit);
  const loading = actions.isLoading || flows.isLoading;
  return (
    <>
      <PageHeader title={t("exec.title")} description="Toutes les exécutions d'actions et de workflows de votre espace, des plus récentes aux plus anciennes." />
      <div className="toolbar-row" role="search">
        <div className="field"><label htmlFor="ex-q">Rechercher</label><input id="ex-q" type="search" value={q} onChange={(e) => { setQ(e.target.value); setLimit(50); }} /></div>
        <div className="field"><label htmlFor="ex-s">Statut</label><select id="ex-s" value={status} onChange={(e) => { setStatus(e.target.value); setLimit(50); }}><option value="">Tous</option><option value="running">En cours</option><option value="succeeded">Réussies</option><option value="failed">Échouées</option><option value="cancelled">Annulées</option></select></div>
        <div className="field"><label htmlFor="ex-k">Type</label><select id="ex-k" value={kind} onChange={(e) => { setKind(e.target.value); setLimit(50); }}><option value="">Tous</option><option value="Workflow">Workflows</option><option value="Action">Actions</option></select></div>
        <p className="hint" role="status">{shownAll.length} exécution(s)</p>
      </div>
      {loading && <p role="status">{t("common.loading")}</p>}
      {(actions.isError || flows.isError) && <p role="alert">{t("common.error")}</p>}
      {!loading && rows.length === 0 && <p>{t("exec.empty")}</p>}
      {rows.length > 0 && (
        <div className="table-wrap">
        <table>
          <caption className="sr-only">{t("exec.title")}</caption>
          <thead><tr><th scope="col">Automatisation</th><th scope="col">Type</th><th scope="col">{t("exec.status")}</th><th scope="col">Environnement</th><th scope="col">Lancée le</th></tr></thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.key}>
                <td><Link to={r.to}>{r.name}</Link></td><td>{r.kind}</td><td><StatusBadge status={r.status} label={r.label} /></td>
                <td>{t(`env.${r.env}` as never)}</td><td>{formatDate(r.at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
      {shownAll.length > limit && <p><button type="button" onClick={() => setLimit(limit + 100)}>Afficher plus ({shownAll.length - limit} restantes)</button></p>}
      {rows.length > 0 && shownAll.length === 0 && <p className="empty-state">Aucune exécution ne correspond à ces filtres.</p>}
    </>
  );
}
