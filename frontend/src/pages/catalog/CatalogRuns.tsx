import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../../api";
import { Badge, EmptyRow, type Tone } from "../../components/AdminUi";
import { PageHeader } from "../../components/PageHeader";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";

interface Row {
  id: string; workflow: { slug: string; name: string; version: string }; status: string; status_label: string;
  created_at: string; finished_at: string | null; progress: { done: number; total: number };
}
interface Detail extends Row { inputs: Record<string, unknown>; masked_keys: string[]; outputs: unknown; error: string; can_relaunch: boolean }

const ACTIVE = ["queued", "running", "waiting", "compensating", "pending"];
const tone = (s: string): Tone => (s === "succeeded" ? "ok" : s === "failed" || s === "cancelled" ? "bad" : ACTIVE.includes(s) ? "warn" : "muted");
const pct = (p: Row["progress"]) => (p.total ? Math.round((p.done / p.total) * 100) : 0);

function Bar({ run }: { run: Row }) {
  return (
    <span className="cr-bar">
      <progress max={Math.max(run.progress.total, 1)} value={run.progress.done} aria-label={`Progression de ${run.workflow.name} : ${pct(run.progress)} %`} />
      <span className="hint">{pct(run.progress)} %</span>
    </span>
  );
}

/** « Mes exécutions » for the Catalogue role: only what this person launched, in a reduced form (name, time, progress). */
export function CatalogRunsPage() {
  const { workspace } = useApp();
  const q = useQuery({
    queryKey: ["catalog-runs", workspace.slug], queryFn: () => api<{ results: Row[] }>(`/workspaces/${workspace.slug}/catalog-runs/`),
    refetchInterval: (query) => ((query.state.data?.results ?? []).some((r) => ACTIVE.includes(r.status)) ? 3000 : false),
  });
  const rows = q.data?.results ?? [];
  return (
    <>
      <PageHeader title="Mes exécutions" description="Ce que vous avez lancé depuis le catalogue, avec son avancement." />
      <section className="adm-card">
        <div className="table-wrap">
          <table className="adm-table">
            <caption className="sr-only">Mes exécutions</caption>
            <thead><tr><th scope="col">Automatisation</th><th scope="col">Lancée le</th><th scope="col">État</th><th scope="col">Progression</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td><Link className="lst-title" to={`/executions/${r.id}`}>{r.workflow.name}</Link></td>
                  <td><time dateTime={r.created_at}>{formatDate(r.created_at)}</time></td>
                  <td><Badge tone={tone(r.status)}>{r.status_label}</Badge></td>
                  <td><Bar run={r} /></td>
                </tr>
              ))}
              {rows.length === 0 && <EmptyRow cols={4}>{q.isLoading ? "Chargement…" : <>Vous n'avez encore rien lancé. <Link to="/catalog">Ouvrir le catalogue</Link>.</>}</EmptyRow>}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

const show = (v: unknown): string => (typeof v === "string" ? v : v === undefined || v === null ? "—" : JSON.stringify(v, null, 2));

/** One of my runs: what I entered (secrets masked), what came back, and a way to launch it again with the same data. */
export function CatalogRunDetailPage() {
  const { id = "" } = useParams();
  const { workspace } = useApp();
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: ["catalog-run", workspace.slug, id], queryFn: () => api<Detail>(`/workspaces/${workspace.slug}/catalog-runs/${id}/`),
    refetchInterval: (query) => (query.state.data && ACTIVE.includes(query.state.data.status) ? 3000 : false), retry: false,
  });
  if (q.isError) return <><PageHeader title="Exécution introuvable" /><p><Link to="/executions">Retour à mes exécutions</Link></p></>;
  const r = q.data;
  if (!r) return <p role="status">Chargement…</p>;
  const entered = Object.entries(r.inputs ?? {});
  const relaunch = () => {
    const prefill = Object.fromEntries(entered.filter(([k]) => !r.masked_keys.includes(k)));
    navigate(`/catalog/${r.workflow.slug}`, { state: { prefill, masked: r.masked_keys } });
  };
  return (
    <>
      <nav aria-label="Fil d'Ariane" className="breadcrumb"><Link to="/executions">Mes exécutions</Link> / <span aria-current="page">{r.workflow.name}</span></nav>
      <PageHeader title={r.workflow.name} description={`Lancée le ${formatDate(r.created_at)}`}
        actions={r.can_relaunch ? <button type="button" className="primary" onClick={relaunch}>Relancer avec ces données</button> : undefined} />
      <p role="status"><Badge tone={tone(r.status)}>{r.status_label}</Badge> <Bar run={r} /></p>
      {r.error && <p role="alert" className="field-error">{r.error}</p>}
      {!r.can_relaunch && <p className="hint" role="note">Vous ne pouvez plus relancer cette automatisation (droit retiré ou retirée du catalogue).</p>}
      <section className="card" aria-labelledby="cr-in">
        <h2 id="cr-in">Données saisies</h2>
        {entered.length === 0 ? <p className="hint">Aucune donnée n'a été saisie.</p> : (
          <dl className="facts">{entered.map(([k, v]) => <div key={k} className="fact"><dt>{k}</dt><dd className={typeof v === "string" ? undefined : "mono"}><span style={{ whiteSpace: "pre-wrap" }}>{show(v)}</span></dd></div>)}</dl>
        )}
        {r.masked_keys.length > 0 && <p className="hint">Les mots de passe et secrets sont masqués : ressaisissez-les si vous relancez.</p>}
      </section>
      <section className="card" aria-labelledby="cr-out">
        <h2 id="cr-out">Retour du workflow</h2>
        {r.outputs === null || r.outputs === undefined || (typeof r.outputs === "object" && Object.keys(r.outputs as object).length === 0)
          ? <p className="hint">{ACTIVE.includes(r.status) ? "Pas encore de résultat : le traitement est en cours." : "Aucun résultat."}</p>
          : <pre className="ctl-json" tabIndex={0} aria-label="Retour du workflow">{JSON.stringify(r.outputs, null, 2)}</pre>}
      </section>
    </>
  );
}
