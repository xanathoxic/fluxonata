import { useMemo, useState } from "react";
import { Badge, EmptyRow, SearchBox, Section, usePaged, type Tone } from "../../components/AdminUi";
import { Overlay } from "../../components/Overlay";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { useWsQuery } from "../../lib";

interface Ev { id: number; created_at: string; actor_label: string; action: string; target_type: string; target_id: string; data: Record<string, unknown> }
interface Audit { results: Ev[] }
const KINDS = ["auth", "execution", "workflow", "workflow_run", "approval", "promotion", "policy", "token", "member", "secret", "connection", "package", "ai", "schedule", "webhook"];
const TONE: Record<string, Tone> = { auth: "info", token: "warn", secret: "warn", policy: "warn", member: "info", promotion: "ok", approval: "ok" };

export function AuditTab() {
  const { workspace } = useApp();
  const [prefix, setPrefix] = useState("");
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<Ev | null>(null);
  const q = useWsQuery<Audit>(`audit-${prefix}`, `/audit/?action=${encodeURIComponent(prefix)}`);
  const rows = useMemo(() => (q.data?.results ?? []).filter((e) => !search.trim() || `${e.actor_label} ${e.action} ${e.target_type} ${e.target_id}`.toLowerCase().includes(search.trim().toLowerCase())), [q.data, search]);
  const paged = usePaged(rows, 25);
  return (
    <Section title="Journal d'audit" hint="Journal applicatif en ajout seul : il n'est pas protégé contre un administrateur de la base de données. Exportez-le régulièrement vers un stockage indépendant si une exigence de preuve l'impose."
      actions={<a className="button" href={`/api/v1/workspaces/${workspace.slug}/audit/export/`} download>Exporter (NDJSON)</a>}>
      <div className="adm-filters">
        <SearchBox label="Rechercher dans le journal" value={search} onChange={setSearch} />
        <div className="field"><label htmlFor="a-f">Filtrer par type d'événement</label>
          <select id="a-f" value={prefix} onChange={(e) => setPrefix(e.target.value)}>
            <option value="">Tous</option>
            {KINDS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select></div>
      </div>
      <div className="table-wrap">
        <table className="adm-table">
          <caption className="sr-only">Événements d'audit</caption>
          <thead><tr><th scope="col">Date</th><th scope="col">Qui</th><th scope="col">Événement</th><th scope="col">Cible</th><th scope="col">Détails</th></tr></thead>
          <tbody>
            {paged.slice.map((e) => (
              <tr key={e.id}><td>{formatDate(e.created_at)}</td><td>{e.actor_label || "—"}</td>
                <td><Badge tone={TONE[e.action.split(".")[0] ?? ""] ?? "muted"}><span className="mono">{e.action}</span></Badge></td>
                <td>{e.target_type} {e.target_id}</td>
                <td><button type="button" onClick={() => setOpen(e)} aria-label={`Voir le détail de l'événement ${e.action} du ${formatDate(e.created_at)}`}>Voir</button></td></tr>
            ))}
            {rows.length === 0 && <EmptyRow cols={5}>{q.isLoading ? "Chargement…" : "Aucun événement ne correspond."}</EmptyRow>}
          </tbody>
        </table>
      </div>
      {paged.pager("Pagination du journal")}
      {open && (
        <Overlay title="Détail de l'événement" description={`${open.action} — ${formatDate(open.created_at)}`} onClose={() => setOpen(null)} footer={<button type="button" onClick={() => setOpen(null)}>Fermer</button>}>
          <dl className="adm-dl"><dt>Qui</dt><dd>{open.actor_label || "—"}</dd><dt>Événement</dt><dd className="mono">{open.action}</dd><dt>Cible</dt><dd>{open.target_type} {open.target_id}</dd></dl>
          <pre className="adm-json">{JSON.stringify(open.data, null, 2)}</pre>
        </Overlay>
      )}
    </Section>
  );
}
