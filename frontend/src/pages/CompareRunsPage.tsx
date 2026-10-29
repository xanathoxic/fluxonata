import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import { useApp } from "../context";
import { formatDate } from "../i18n";

interface Side { id: string; workflow: string; version: string; environment: string; status: string; created_at: string; duration_s: number | null }
interface Pair { key: string; a: unknown; b: unknown; same: boolean }
interface StepDiff { node_id: string; name: string; status_a: string | null; status_b: string | null; same_status: boolean; same_output: boolean; output_diff: Pair[]; error_a: string | null; error_b: string | null }
interface Comparison { a: Side; b: Side; same_version: boolean; inputs: Pair[]; steps: StepDiff[]; outputs: Pair[]; different_steps: number }

const show = (v: unknown) => (v === undefined || v === null ? "—" : typeof v === "string" ? v : JSON.stringify(v));

function PairTable({ caption, rows }: { caption: string; rows: Pair[] }) {
  if (rows.length === 0) return <p className="hint">{caption} : aucune donnée.</p>;
  return (
    <div className="table-wrap"><table><caption>{caption}</caption>
      <thead><tr><th scope="col">Champ</th><th scope="col">Exécution A</th><th scope="col">Exécution B</th></tr></thead>
      <tbody>{rows.map((r) => <tr key={r.key} className={r.same ? undefined : "diff-row"}><th scope="row">{r.key}{r.same ? "" : " ≠"}</th><td className="mono">{show(r.a)}</td><td className="mono">{show(r.b)}</td></tr>)}</tbody>
    </table></div>
  );
}

/** Two runs side by side: what changed in the inputs, which steps behaved differently, and how their outputs differ. */
export function CompareRunsPage() {
  const { workspace } = useApp();
  const [params] = useSearchParams();
  const a = params.get("a") ?? "";
  const b = params.get("b") ?? "";
  const q = useQuery({ queryKey: ["compare", workspace.slug, a, b], queryFn: () => api<Comparison>(`/workspaces/${workspace.slug}/workflow-runs/compare/?a=${a}&b=${b}`), enabled: Boolean(a && b) });
  const c = q.data;
  return (
    <>
      <nav aria-label="Fil d'Ariane" className="breadcrumb"><Link to="/executions">Exécutions</Link> / <span aria-current="page">Comparaison</span></nav>
      <PageHeader title="Comparer deux exécutions" description="Entrées, déroulement et sorties, côte à côte. Les différences sont signalées par ≠." />
      {!a || !b ? <p role="alert">Choisissez deux exécutions dans la liste.</p> : null}
      {q.isLoading && <p role="status">Chargement…</p>}
      {q.isError && <p role="alert">Comparaison impossible : exécution introuvable ou non autorisée.</p>}
      {c && (
        <>
          <div className="table-wrap"><table><caption className="sr-only">Les deux exécutions</caption>
            <thead><tr><th scope="col"></th><th scope="col">Exécution A</th><th scope="col">Exécution B</th></tr></thead>
            <tbody>
              <tr><th scope="row">Lien</th><td><Link to={`/runs/${c.a.id}`}>{c.a.workflow} v{c.a.version}</Link></td><td><Link to={`/runs/${c.b.id}`}>{c.b.workflow} v{c.b.version}</Link></td></tr>
              <tr><th scope="row">Statut</th><td><StatusBadge status={c.a.status} label={c.a.status} /></td><td><StatusBadge status={c.b.status} label={c.b.status} /></td></tr>
              <tr><th scope="row">Lancée le</th><td>{formatDate(c.a.created_at)}</td><td>{formatDate(c.b.created_at)}</td></tr>
              <tr><th scope="row">Durée (s)</th><td>{c.a.duration_s ?? "—"}</td><td>{c.b.duration_s ?? "—"}</td></tr>
            </tbody></table></div>
          {!c.same_version && <p role="note" className="notice">Ces exécutions n'utilisent pas la même version du workflow : les étapes peuvent différer.</p>}
          <p role="status">{c.different_steps === 0 ? "Les étapes se comportent à l'identique." : `${c.different_steps} étape(s) se comportent différemment.`}</p>
          <PairTable caption="Entrées" rows={c.inputs} />
          <div className="table-wrap"><table><caption>Étapes</caption>
            <thead><tr><th scope="col">Étape</th><th scope="col">Statut A</th><th scope="col">Statut B</th><th scope="col">Sortie</th></tr></thead>
            <tbody>{c.steps.map((s) => (
              <tr key={s.node_id} className={s.same_status && s.same_output ? undefined : "diff-row"}>
                <th scope="row">{s.name}{s.same_status && s.same_output ? "" : " ≠"}</th>
                <td>{s.status_a ?? "non exécutée"}{s.error_a ? ` — ${s.error_a}` : ""}</td><td>{s.status_b ?? "non exécutée"}{s.error_b ? ` — ${s.error_b}` : ""}</td>
                <td>{s.same_output ? "identique" : s.output_diff.map((d) => <div key={d.key} className="mono">{d.key} : {show(d.a)} → {show(d.b)}</div>)}</td>
              </tr>))}</tbody></table></div>
          <PairTable caption="Sorties du workflow" rows={c.outputs} />
        </>
      )}
    </>
  );
}
