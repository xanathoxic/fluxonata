import { Link } from "react-router-dom";
import type { Page, WorkflowRun } from "../../api";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDate } from "../../i18n";
import { useWsQuery } from "../../lib";
import { useDraft } from "../DraftContext";

export function RunsTab() {
  const { slug } = useDraft();
  const q = useWsQuery<Page<WorkflowRun>>(`wf-runs-${slug}`, `/workflow-runs/?workflow=${slug}`, { refetchInterval: 4000 });
  return (
    <section aria-label="Exécutions du workflow">
      <h2>Toutes les exécutions</h2>
      <div className="table-wrap">
        <table>
          <caption className="sr-only">Exécutions de ce workflow</caption>
          <thead><tr><th scope="col">Lancée le</th><th scope="col">Version</th><th scope="col">Statut</th><th scope="col">Environnement</th><th scope="col">Par</th></tr></thead>
          <tbody>
            {q.data?.results.map((r) => (
              <tr key={r.id}><td><Link to={`/workflows/${slug}/follow?run=${r.id}`}>{formatDate(r.created_at)}</Link></td><td>{r.workflow.version}</td><td><StatusBadge status={r.status} label={r.needs_decision ? "Décision requise" : r.status_label} /></td><td>{r.environment}</td><td>{r.requested_by}</td></tr>
            ))}
            {q.data?.results.length === 0 && <tr><td colSpan={5} className="hint">Aucune exécution.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
