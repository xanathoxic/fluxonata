import { useState } from "react";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { useWsQuery } from "../../lib";

interface Audit { results: { id: number; created_at: string; actor_label: string; action: string; target_type: string; target_id: string; data: Record<string, unknown> }[] }

export function AuditTab() {
  const { workspace } = useApp();
  const [prefix, setPrefix] = useState("");
  const q = useWsQuery<Audit>(`audit-${prefix}`, `/audit/?action=${encodeURIComponent(prefix)}`);
  return (
    <>
      <h2>Journal d'audit</h2>
      <p className="hint">Journal applicatif en ajout seul : il n'est pas protégé contre un administrateur de la base de données. Exportez-le régulièrement vers un stockage indépendant si une exigence de preuve l'impose.</p>
      <div className="inline-form">
        <div className="field"><label htmlFor="a-f">Filtrer par type d'événement</label>
          <select id="a-f" value={prefix} onChange={(e) => setPrefix(e.target.value)}>
            <option value="">Tous</option>
            {["auth", "execution", "workflow", "workflow_run", "approval", "promotion", "policy", "token", "member", "secret", "connection", "package", "ai", "schedule", "webhook"].map((p) => <option key={p} value={p}>{p}</option>)}
          </select></div>
        <a className="button" href={`/api/v1/workspaces/${workspace.slug}/audit/export/`} download>Exporter (NDJSON)</a>
      </div>
      <div className="table-wrap">
        <table>
          <caption className="sr-only">Événements d'audit</caption>
          <thead><tr><th scope="col">Date</th><th scope="col">Qui</th><th scope="col">Événement</th><th scope="col">Cible</th><th scope="col">Détails</th></tr></thead>
          <tbody>
            {q.data?.results.map((e) => (
              <tr key={e.id}><td>{formatDate(e.created_at)}</td><td>{e.actor_label || "—"}</td><td className="mono">{e.action}</td><td>{e.target_type} {e.target_id}</td>
                <td><details><summary>Voir</summary><pre>{JSON.stringify(e.data, null, 2)}</pre></details></td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
