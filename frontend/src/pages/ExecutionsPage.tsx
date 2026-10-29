import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { apiAll, type Execution, type WorkflowRun } from "../api";
import { Pagination } from "../components/Pagination";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import { useApp } from "../context";
import { formatDate, t } from "../i18n";

interface Row { key: string; to: string; name: string; kind: string; status: string; label: string; env: string; at: string }

const STATUSES: [string, string][] = [["", "Toutes"], ["running", "En cours"], ["succeeded", "Réussies"], ["failed", "Échouées"], ["cancelled", "Annulées"]];
const KINDS: [string, string][] = [["", "Tous"], ["Workflow", "Workflows"], ["Action", "Actions"]];
const ENVS: Record<string, string> = { dev: "Développement", test: "Recette", prod: "Production" };

/** « il y a 5 min » for recent runs, the date afterwards: what people look for is « the one I just started ». */
function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 45) return "à l'instant";
  if (s < 3600) return `il y a ${Math.round(s / 60)} min`;
  if (s < 86400) return `il y a ${Math.round(s / 3600)} h`;
  if (s < 7 * 86400) return `il y a ${Math.round(s / 86400)} j`;
  return new Date(iso).toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
}

const Magnifier = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
);

export function ExecutionsPage() {
  const { workspace } = useApp();
  const actions = useQuery({ queryKey: ["executions", workspace.slug], queryFn: () => apiAll<Execution>(`/workspaces/${workspace.slug}/executions/`, 500), refetchInterval: 3000 });
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [env, setEnv] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [minDur, setMinDur] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [filters, setFilters] = useState(true);
  const navigate = useNavigate();
  const serverQs = new URLSearchParams(Object.entries({ q, status, environment: env, from, to, min_duration: minDur }).filter(([, v]) => v)).toString();
  const flows = useQuery({ queryKey: ["wruns", workspace.slug, serverQs], queryFn: () => apiAll<WorkflowRun>(`/workspaces/${workspace.slug}/workflow-runs/${serverQs ? `?${serverQs}` : ""}`, 500), refetchInterval: 3000 });
  const rows: Row[] = [
    ...(actions.data?.results ?? []).map((e) => ({ key: e.id, to: `/executions/${e.id}`, name: e.action.name, kind: "Action", status: e.status, label: e.status_label, env: e.environment, at: e.created_at })),
    ...(flows.data?.results ?? []).map((r) => ({ key: r.id, to: `/runs/${r.id}`, name: r.workflow.name, kind: "Workflow", status: r.status, label: r.needs_decision ? "Décision requise" : r.status_label, env: r.environment, at: r.created_at })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  const byText = rows.filter((r) => (!q || r.name.toLowerCase().includes(q.toLowerCase())) && (!kind || r.kind === kind));
  const count = (s: string) => (s ? byText.filter((r) => r.status === s).length : byText.length);
  const shownAll = byText.filter((r) => !status || r.status === status);
  const pages = Math.max(1, Math.ceil(shownAll.length / pageSize));
  const cur = Math.min(page, pages);
  const shown = shownAll.slice((cur - 1) * pageSize, cur * pageSize);
  const loading = actions.isLoading || flows.isLoading;
  const reset = <T,>(set: (v: T) => void) => (v: T) => { set(v); setPage(1); };
  const active = [
    q && { k: "q", label: `Texte : « ${q} »`, clear: () => reset(setQ)("") },
    kind && { k: "kind", label: `Type : ${kind === "Workflow" ? "workflows" : "actions"}`, clear: () => reset(setKind)("") },
    env && { k: "env", label: `Environnement : ${ENVS[env]}`, clear: () => reset(setEnv)("") },
    from && { k: "from", label: `Depuis le ${from}`, clear: () => reset(setFrom)("") },
    to && { k: "to", label: `Jusqu'au ${to}`, clear: () => reset(setTo)("") },
    minDur && { k: "dur", label: `Durée ≥ ${minDur} s`, clear: () => reset(setMinDur)("") },
  ].filter(Boolean) as { k: string; label: string; clear: () => void }[];
  const clearAll = () => { setQ(""); setKind(""); setEnv(""); setFrom(""); setTo(""); setMinDur(""); setStatus(""); setPage(1); };
  return (
    <>
      <PageHeader title={t("exec.title")} description="Toutes les exécutions d'actions et de workflows de votre espace, des plus récentes aux plus anciennes."
        actions={<>
          <a className="button" href={`/api/v1/workspaces/${workspace.slug}/workflow-runs/export/${serverQs ? `?${serverQs}` : ""}`} download>Exporter en CSV</a>
          <button type="button" disabled={picked.length !== 2} onClick={() => navigate(`/runs/compare?a=${picked[0]}&b=${picked[1]}`)}>Comparer les 2 exécutions cochées</button>
        </>} />
      <section className="xsearch" role="search" aria-label="Rechercher dans les exécutions">
        <div className="xs-box">
          <Magnifier />
          <label htmlFor="ex-q" className="sr-only">Rechercher</label>
          <input id="ex-q" type="search" value={q} placeholder="Rechercher une automatisation par son nom…" autoComplete="off" onChange={(e) => reset(setQ)(e.target.value)} />
          <button type="button" className="xs-toggle" aria-expanded={filters} aria-controls="ex-filters" onClick={() => setFilters(!filters)}>{filters ? "Masquer les filtres" : "Afficher les filtres"}</button>
        </div>
        <div className="xs-row">
          <div className="xs-chips" role="group" aria-label="Statut">
            {STATUSES.map(([v, l]) => <button key={v} type="button" className={`xs-chip xs-${v || "all"}`} aria-pressed={status === v} onClick={() => reset(setStatus)(v)}>{l} <span className="xs-n">{count(v)}</span></button>)}
          </div>
          <div className="xs-seg" role="group" aria-label="Type">
            {KINDS.map(([v, l]) => <button key={v} type="button" aria-pressed={kind === v} onClick={() => reset(setKind)(v)}>{l}</button>)}
          </div>
        </div>
        {filters && (
          <div id="ex-filters" className="xs-filters">
            <div className="field"><label htmlFor="ex-env">Environnement</label><select id="ex-env" value={env} onChange={(e) => reset(setEnv)(e.target.value)}><option value="">Tous</option><option value="dev">Développement</option><option value="test">Recette</option><option value="prod">Production</option></select></div>
            <div className="field"><label htmlFor="ex-from">Du</label><input id="ex-from" type="date" value={from} onChange={(e) => reset(setFrom)(e.target.value)} /></div>
            <div className="field"><label htmlFor="ex-to">Au</label><input id="ex-to" type="date" value={to} onChange={(e) => reset(setTo)(e.target.value)} /></div>
            <div className="field"><label htmlFor="ex-dur">Durée minimale (s)</label><input id="ex-dur" type="number" min={0} value={minDur} onChange={(e) => reset(setMinDur)(e.target.value)} /></div>
          </div>
        )}
        {active.length > 0 && (
          <div className="xs-active" aria-label="Filtres actifs">
            {active.map((a) => <span key={a.k} className="xs-tag">{a.label}<button type="button" onClick={a.clear} aria-label={`Retirer le filtre : ${a.label}`}>×</button></span>)}
            <button type="button" className="xs-clear" onClick={clearAll}>Tout effacer</button>
          </div>
        )}
      </section>
      {loading && <p role="status">{t("common.loading")}</p>}
      {(actions.isError || flows.isError) && <p role="alert">{t("common.error")}</p>}
      {!loading && rows.length === 0 && <p className="empty-state">{t("exec.empty")}</p>}
      {rows.length > 0 && (
        <div className="table-wrap xtable">
          <table>
            <caption className="sr-only">{t("exec.title")} — {shownAll.length} exécution(s)</caption>
            <thead><tr><th scope="col"><span className="sr-only">Comparer</span></th><th scope="col">Automatisation</th><th scope="col">Type</th><th scope="col">{t("exec.status")}</th><th scope="col">Environnement</th><th scope="col">Lancée</th></tr></thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.key} className={`xrow-${r.status}`}>
                  <td>{r.kind === "Workflow" && <input type="checkbox" aria-label={`Comparer ${r.name} du ${formatDate(r.at)}`} checked={picked.includes(r.key)} disabled={!picked.includes(r.key) && picked.length >= 2} onChange={(e) => setPicked(e.target.checked ? [...picked, r.key] : picked.filter((k) => k !== r.key))} />}</td>
                  <td><Link to={r.to} className="xname">{r.name}</Link></td>
                  <td><span className={`kind kind-${r.kind.toLowerCase()}`}>{r.kind}</span></td>
                  <td><StatusBadge status={r.status} label={r.label} /></td>
                  <td><span className={`env env-${r.env}`}>{t(`env.${r.env}` as never)}</span></td>
                  <td><time dateTime={r.at} title={formatDate(r.at)}>{ago(r.at)}</time><span className="xdate">{formatDate(r.at)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > 0 && shownAll.length === 0 && (
        <div className="ops-empty"><strong>Aucune exécution ne correspond à ces filtres</strong><p className="hint">Essayez un autre mot, une autre période ou retirez un filtre.</p><button type="button" className="primary" onClick={clearAll}>Tout effacer</button></div>
      )}
      {shownAll.length > 0 && <Pagination total={shownAll.length} page={cur} pageSize={pageSize} onPage={setPage} onPageSize={(n) => { setPageSize(n); setPage(1); }} label="Pagination des exécutions" />}
      {(rows.length >= 500) && <p className="hint">Les 500 exécutions les plus récentes de chaque type sont chargées : affinez les filtres pour remonter plus loin.</p>}
    </>
  );
}
