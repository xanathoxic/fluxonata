import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, apiAll, type Approval, type Page, type WorkflowRun, type WorkflowSummary } from "../api";
import { useFavorites } from "../components/FavoriteStar";
import { StatusBadge } from "../components/StatusBadge";
import { useApp } from "../context";
import { formatDate } from "../i18n";

export function HomePage() {
  const { workspace, me, can } = useApp();
  const approvals = useQuery({ queryKey: ["approvals", workspace.slug, "pending"], queryFn: () => api<Page<Approval>>(`/workspaces/${workspace.slug}/approvals/?status=pending`), enabled: can("approve") });
  const runs = useQuery({ queryKey: ["wruns", workspace.slug], queryFn: () => api<Page<WorkflowRun>>(`/workspaces/${workspace.slug}/workflow-runs/`) });
  const wfs = useQuery({ queryKey: ["workflows", workspace.slug], queryFn: () => apiAll<WorkflowSummary>(`/workspaces/${workspace.slug}/workflows/`) });
  const offered = useQuery({ queryKey: ["catalog", workspace.slug, ""], queryFn: () => api<{ results: { slug: string; name: string }[] }>(`/workspaces/${workspace.slug}/catalog/`) });
  const favs = useFavorites();
  const pending = approvals.data?.results.filter((a) => a.can_decide) ?? [];
  const decisions = (runs.data?.results ?? []).filter((r) => r.needs_decision);
  const running = (runs.data?.results ?? []).filter((r) => ["running", "waiting", "pending", "queued"].includes(r.status)).length;
  const failed = (runs.data?.results ?? []).filter((r) => r.status === "failed").length;
  return (
    <>
      <h1>Bonjour {me.username}</h1>
      <p>Espace « {workspace.name} » — votre rôle : {workspace.role.replace("_", " ")}.</p>
      <ul className="stat-grid" aria-label="Vue d'ensemble">
        <li><Link className="stat" to="/workflows"><span className="stat-icon" aria-hidden="true">⇄</span><span><span className="stat-n">{wfs.data?.results.length ?? "–"}</span><span className="stat-l">Workflows</span></span></Link></li>
        <li><Link className="stat ok" to="/executions"><span className="stat-icon" aria-hidden="true">▶</span><span><span className="stat-n">{runs.data ? running : "–"}</span><span className="stat-l">Exécutions en cours</span></span></Link></li>
        <li><Link className="stat violet" to="/catalog"><span className="stat-icon" aria-hidden="true">◇</span><span><span className="stat-n">{offered.data?.results.length ?? "–"}</span><span className="stat-l">Automatisations au catalogue</span></span></Link></li>
        <li><Link className="stat warn" to="/executions"><span className="stat-icon" aria-hidden="true">!</span><span><span className="stat-n">{runs.data ? failed : "–"}</span><span className="stat-l">Échecs récents</span></span></Link></li>
      </ul>
      {(pending.length > 0 || decisions.length > 0) && (
        <section aria-labelledby="todo" className="card">
          <h2 id="todo">À traiter</h2>
          <ul>
            {pending.length > 0 && <li><Link to="/approvals">{pending.length} approbation(s) en attente</Link></li>}
            {decisions.map((r) => <li key={r.id}><Link to={`/runs/${r.id}`}>« {r.workflow.name} » attend une décision (issue incertaine)</Link></li>)}
          </ul>
        </section>
      )}
      {favs.data && favs.data.results.length > 0 && (
        <section aria-labelledby="favs" className="card">
          <h2 id="favs">Favoris</h2>
          <ul className="plain">{favs.data.results.map((f) => <li key={`${f.kind}${f.slug}`}><span aria-hidden="true">★</span> <Link to={f.kind === "workflow" ? `/workflows/${f.slug}` : `/library/actions/${f.slug}`}>{f.name}</Link> <span className="hint">{f.kind === "workflow" ? "workflow" : "action"}</span></li>)}</ul>
        </section>
      )}
      <section aria-labelledby="quick">
        <h2 id="quick">Démarrer rapidement</h2>
        <ul className="quick-grid">
          <li><Link className="quick" to="/workflows"><span className="stat-icon" aria-hidden="true">+</span><strong>Nouveau workflow</strong><span className="sub">Créer à partir d'un schéma</span></Link></li>
          <li><Link className="quick" to="/catalog"><span className="stat-icon violet" aria-hidden="true">◇</span><strong>Catalogue d'automatisations</strong><span className="sub">Lancer une automatisation prête à l'emploi</span></Link></li>
          <li><Link className="quick" to="/connections"><span className="stat-icon" aria-hidden="true">⌁</span><strong>Connecter un système</strong><span className="sub">API, cloud, infrastructure</span></Link></li>
          <li><Link className="quick" to="/library"><span className="stat-icon" aria-hidden="true">▤</span><strong>Bibliothèques</strong><span className="sub">Versions et dépendances</span></Link></li>
        </ul>
      </section>
      <section aria-labelledby="start" className="card" style={{ marginTop: 24 }}>
        <h2 id="start">Démarrer en 5 minutes</h2>
        <ol>
          <li><Link to="/catalog">Ouvrez le catalogue</Link>, lancez une automatisation (par exemple « Statistiques de texte ») et lisez son résultat.</li>
          <li><Link to="/workflows">Créez un workflow</Link> : ajoutez une action, une condition, puis une seconde action — sans code.</li>
          <li>Publiez-le et lancez-le depuis « Mes exécutions ».</li>
        </ol>
      </section>
      <section aria-labelledby="recent" className="adm-card" style={{ marginTop: 24 }}>
        <header className="adm-card-h"><h2 id="recent">Exécutions récentes</h2><Link className="button" to="/executions">Toutes mes exécutions</Link></header>
        {runs.data?.results.length === 0 && <p>Aucune exécution pour l'instant.</p>}
        <ul className="plain home-runs">
          {runs.data?.results.slice(0, 5).map((r) => (
            <li key={r.id}><StatusBadge status={r.status} label={r.status_label} /> <Link className="lst-title" to={`/runs/${r.id}`}>{r.workflow.name}</Link> <span className="hint">{formatDate(r.created_at)}</span></li>
          ))}
        </ul>
      </section>
    </>
  );
}
