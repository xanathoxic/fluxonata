import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, apiAll, type ChildRun, type WorkflowRun } from "../api";
import { Pagination } from "../components/Pagination";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import { useApp } from "../context";
import { formatDate, t } from "../i18n";

interface Row { key: string; to: string; name: string; kind: string; status: string; label: string; env: string; at: string; children?: boolean }

const STATUSES: [string, string][] = [["", "Toutes"], ["running", "En cours"], ["succeeded", "Réussies"], ["failed", "Échouées"], ["cancelled", "Annulées"]];
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

/** Chevron de repli : pointe à droite quand la ligne est repliée, vers le bas quand elle est dépliée. */
function Fold({ open, onToggle, label }: { open: boolean; onToggle: () => void; label: string }) {
  return (
    <button type="button" className="xfold" aria-expanded={open} aria-label={label} onClick={onToggle}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" style={{ transform: open ? "rotate(90deg)" : undefined }}><path d="m9 6 6 6-6 6" /></svg>
    </button>
  );
}

/** Lignes des sous-workflows d'une exécution (chargées au dépliage, suivies tant qu'elles sont ouvertes) ; chacune peut se déplier à son tour. */
function ChildRows({ parent, depth, open, toggle }: { parent: string; depth: number; open: Set<string>; toggle: (id: string) => void }) {
  const { workspace } = useApp();
  const kids = useQuery({ queryKey: ["wrun-children", workspace.slug, parent], queryFn: () => api<ChildRun[]>(`/workspaces/${workspace.slug}/workflow-runs/${parent}/children/`), refetchInterval: 3000 });
  if (kids.isLoading) return <tr className="xchild"><td /><td colSpan={5} style={{ paddingLeft: `${depth * 24}px` }}><span role="status" className="hint">{t("common.loading")}</span></td></tr>;
  if (kids.isError) return <tr className="xchild"><td /><td colSpan={5} style={{ paddingLeft: `${depth * 24}px` }}><span role="alert" className="hint">Les sous-workflows n'ont pas pu être chargés.</span></td></tr>;
  return (
    <>
      {(kids.data ?? []).map((c) => {
        const isOpen = open.has(c.id);
        const name = c.workflow.name;
        return (
          <Fragment key={c.id}>
            <tr className={`xrow-${c.status} xchild`}>
              <td />
              <td style={{ paddingLeft: `${depth * 24}px` }}>
                <span className="xnamecell">
                  {c.has_children ? <Fold open={isOpen} onToggle={() => toggle(c.id)} label={`${isOpen ? "Replier" : "Déplier"} les sous-workflows de ${name}`} /> : <span className="xfold-gap" />}
                  <Link to={`/runs/${c.id}`} className="xname">{name}</Link>
                  <span className="hint"> · étape « {c.step_name} »{c.child_index !== null ? ` · n° ${c.child_index + 1}` : ""}</span>
                </span>
              </td>
              <td><span className="kind kind-sous-workflow">Sous-workflow</span></td>
              <td><StatusBadge status={c.status} label={c.needs_decision ? "Décision requise" : c.status_label} /></td>
              <td><span className={`env env-${c.environment}`}>{t(`env.${c.environment}` as never)}</span></td>
              <td><time dateTime={c.created_at} title={formatDate(c.created_at)}>{ago(c.created_at)}</time><span className="xdate">{formatDate(c.created_at)}</span></td>
            </tr>
            {isOpen && <ChildRows parent={c.id} depth={depth + 1} open={open} toggle={toggle} />}
          </Fragment>
        );
      })}
    </>
  );
}

const Magnifier = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
);

export function ExecutionsPage() {
  const { workspace } = useApp();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [env, setEnv] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [minDur, setMinDur] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggleRow = (id: string) => setExpanded((cur) => { const next = new Set(cur); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [filters, setFilters] = useState(true);
  const navigate = useNavigate();
  const serverQs = new URLSearchParams(Object.entries({ q, status, environment: env, from, to, min_duration: minDur }).filter(([, v]) => v)).toString();
  const flows = useQuery({ queryKey: ["wruns", workspace.slug, serverQs], queryFn: () => apiAll<WorkflowRun>(`/workspaces/${workspace.slug}/workflow-runs/${serverQs ? `?${serverQs}` : ""}`, 500), refetchInterval: 3000 });
  const rows: Row[] = [
    ...(flows.data?.results ?? []).map((r) => ({ key: r.id, to: `/runs/${r.id}`, name: r.workflow.name, kind: "Workflow", status: r.status, label: r.needs_decision ? "Décision requise" : r.status_label, env: r.environment, at: r.created_at, children: r.has_children })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  const byText = rows.filter((r) => (!q || r.name.toLowerCase().includes(q.toLowerCase())));
  const count = (s: string) => (s ? byText.filter((r) => r.status === s).length : byText.length);
  const shownAll = byText.filter((r) => !status || r.status === status);
  const pages = Math.max(1, Math.ceil(shownAll.length / pageSize));
  const cur = Math.min(page, pages);
  const shown = shownAll.slice((cur - 1) * pageSize, cur * pageSize);
  const loading = flows.isLoading;
  const reset = <T,>(set: (v: T) => void) => (v: T) => { set(v); setPage(1); };
  const active = [
    q && { k: "q", label: `Texte : « ${q} »`, clear: () => reset(setQ)("") },
    env && { k: "env", label: `Environnement : ${ENVS[env]}`, clear: () => reset(setEnv)("") },
    from && { k: "from", label: `Depuis le ${from}`, clear: () => reset(setFrom)("") },
    to && { k: "to", label: `Jusqu'au ${to}`, clear: () => reset(setTo)("") },
    minDur && { k: "dur", label: `Durée ≥ ${minDur} s`, clear: () => reset(setMinDur)("") },
  ].filter(Boolean) as { k: string; label: string; clear: () => void }[];
  // le texte, le statut, la période et la durée sont filtrés côté serveur (le type, côté client) : « aucune ligne » avec un filtre actif n'est pas « aucune exécution »
  const filtered = active.length > 0 || status !== "";
  const clearAll = () => { setQ(""); setEnv(""); setFrom(""); setTo(""); setMinDur(""); setStatus(""); setPage(1); };
  return (
    <>
      <PageHeader title={t("exec.title")} description="Toutes les exécutions de workflows de votre espace (leurs sous-workflows se déplient sous chaque ligne), des plus récentes aux plus anciennes."
        actions={<>
          <a className="button" href={`/api/v1/workspaces/${workspace.slug}/workflow-runs/export/${serverQs ? `?${serverQs}` : ""}`} download>Exporter en CSV</a>
          <button type="button" disabled={picked.length !== 2} onClick={() => navigate(`/runs/compare?a=${picked[0]}&b=${picked[1]}`)}>Comparer les 2 exécutions cochées</button>
        </>} />
      {/* biome-ignore lint/a11y/useSemanticElements: rôle explicite conservé ; l'élément natif changerait la mise en forme */}
      <section className="xsearch" role="search" aria-label="Rechercher dans les exécutions">
        <div className="xs-box">
          <Magnifier />
          <label htmlFor="ex-q" className="sr-only">Rechercher</label>
          <input id="ex-q" type="search" value={q} placeholder="Rechercher une automatisation par son nom…" autoComplete="off" onChange={(e) => reset(setQ)(e.target.value)} />
          <button type="button" className="xs-toggle" aria-expanded={filters} aria-controls="ex-filters" onClick={() => setFilters(!filters)}>{filters ? "Masquer les filtres" : "Afficher les filtres"}</button>
        </div>
        <div className="xs-row">
          {/* biome-ignore lint/a11y/useSemanticElements: rôle explicite conservé ; l'élément natif changerait la mise en forme */}
          <div className="xs-chips" role="group" aria-label="Statut">
            {STATUSES.map(([v, l]) => <button key={v} type="button" className={`xs-chip xs-${v || "all"}`} aria-pressed={status === v} onClick={() => reset(setStatus)(v)}>{l} <span className="xs-n">{count(v)}</span></button>)}
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
          // biome-ignore lint/a11y/useAriaPropsSupportedByRole: le libellé nomme le bloc ou l'élément pour les lecteurs d'écran
          <div className="xs-active" aria-label="Filtres actifs">
            {active.map((a) => <span key={a.k} className="xs-tag">{a.label}<button type="button" onClick={a.clear} aria-label={`Retirer le filtre : ${a.label}`}>×</button></span>)}
            <button type="button" className="xs-clear" onClick={clearAll}>Tout effacer</button>
          </div>
        )}
      </section>
      {loading && <p role="status">{t("common.loading")}</p>}
      {flows.isError && <p role="alert">{t("common.error")}</p>}
      {!loading && !flows.isError && rows.length === 0 && !filtered && <p className="empty-state">{t("exec.empty")}</p>}
      {rows.length > 0 && (
        <div className="table-wrap xtable">
          <table>
            <caption className="sr-only">{t("exec.title")} — {shownAll.length} exécution(s)</caption>
            <thead><tr><th scope="col"><span className="sr-only">Comparer</span></th><th scope="col">Automatisation</th><th scope="col">Type</th><th scope="col">{t("exec.status")}</th><th scope="col">Environnement</th><th scope="col">Lancée</th></tr></thead>
            <tbody>
              {shown.map((r) => (
                <Fragment key={r.key}>
                  <tr className={`xrow-${r.status}`}>
                    <td>{r.kind === "Workflow" && <input type="checkbox" aria-label={`Comparer ${r.name} du ${formatDate(r.at)}`} checked={picked.includes(r.key)} disabled={!picked.includes(r.key) && picked.length >= 2} onChange={(e) => setPicked(e.target.checked ? [...picked, r.key] : picked.filter((k) => k !== r.key))} />}</td>
                    <td>
                      <span className="xnamecell">
                        {r.children ? <Fold open={expanded.has(r.key)} onToggle={() => toggleRow(r.key)} label={`${expanded.has(r.key) ? "Replier" : "Déplier"} les sous-workflows de ${r.name}`} /> : <span className="xfold-gap" />}
                        <Link to={r.to} className="xname">{r.name}</Link>
                      </span>
                    </td>
                    <td><span className={`kind kind-${r.kind.toLowerCase()}`}>{r.kind}</span></td>
                    <td><StatusBadge status={r.status} label={r.label} /></td>
                    <td><span className={`env env-${r.env}`}>{t(`env.${r.env}` as never)}</span></td>
                    <td><time dateTime={r.at} title={formatDate(r.at)}>{ago(r.at)}</time><span className="xdate">{formatDate(r.at)}</span></td>
                  </tr>
                  {r.children && expanded.has(r.key) && <ChildRows parent={r.key} depth={1} open={expanded} toggle={toggleRow} />}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!loading && !flows.isError && shownAll.length === 0 && (rows.length > 0 || filtered) && (
        <div className="ops-empty"><strong>Aucune exécution ne correspond à ces filtres</strong><p className="hint">Essayez un autre mot, une autre période ou retirez un filtre.</p><button type="button" className="primary" onClick={clearAll}>Tout effacer</button></div>
      )}
      {shownAll.length > 0 && <Pagination total={shownAll.length} page={cur} pageSize={pageSize} onPage={setPage} onPageSize={(n) => { setPageSize(n); setPage(1); }} label="Pagination des exécutions" />}
      {(rows.length >= 500) && <p className="hint">Les 500 exécutions les plus récentes de chaque type sont chargées : affinez les filtres pour remonter plus loin.</p>}
    </>
  );
}
