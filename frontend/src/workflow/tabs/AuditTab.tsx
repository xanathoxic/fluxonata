import { formatDate } from "../../i18n";
import { useWsQuery } from "../../lib";
import { useDraft } from "../DraftContext";

interface Audit { results: { id: number; created_at: string; actor: string; action: string; details: Record<string, unknown> }[] }

export function AuditTab() {
  const { slug } = useDraft();
  const q = useWsQuery<Audit>(`wf-audit-${slug}`, `/workflows/${slug}/audit/`);
  return (
    <section aria-label="Audit du workflow">
      <h2>Audit</h2>
      <p className="hint">Qui a créé, modifié, publié ou lancé ce workflow. Journal applicatif en ajout seul.</p>
      <div className="table-wrap">
        <table>
          <caption className="sr-only">Événements d'audit de ce workflow</caption>
          <thead><tr><th scope="col">Date</th><th scope="col">Qui</th><th scope="col">Événement</th><th scope="col">Détails</th></tr></thead>
          <tbody>
            {q.data?.results.map((e) => <tr key={e.id}><td>{formatDate(e.created_at)}</td><td>{e.actor || "—"}</td><td className="mono">{e.action}</td><td>{Object.entries(e.details).map(([k, v]) => `${k} : ${String(v)}`).join(" · ") || "—"}</td></tr>)}
            {q.data?.results.length === 0 && <tr><td colSpan={4} className="hint">Aucun événement.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
