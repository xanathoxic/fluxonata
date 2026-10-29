import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type Page, type WorkflowRun } from "../api";
import { StatusBadge } from "../components/StatusBadge";
import { useApp } from "../context";
import { formatDate, t } from "../i18n";
import { CanvasView } from "./CanvasView";
import { RunSchemaView, RunSummary, runGraph } from "./RunSchemaView";

const ACTIVE = ["running", "compensating"];
const BUSY_STEP = ["running", "waiting", "retry_wait", "uncertain"];
const MAX_AGGREGATED = 20;
export const ALL_ACTIVE = "all";

/** Runs of this workflow, newest first; polled while at least one is active (several runs may be active at once). */
export function useWorkflowRuns(slug: string) {
  const { workspace } = useApp();
  return useQuery({
    queryKey: ["wf-runs-follow", workspace.slug, slug],
    queryFn: () => api<Page<WorkflowRun>>(`/workspaces/${workspace.slug}/workflow-runs/?workflow=${slug}`),
    refetchInterval: (q) => (q.state.data?.results.some((r) => ACTIVE.includes(r.status)) ? 2000 : 15000),
  });
}

export function RunPicker({ slug, selected, onSelect }: { slug: string; selected: string | null; onSelect: (id: string | null) => void }) {
  const runs = useWorkflowRuns(slug);
  const list = runs.data?.results ?? [];
  if (list.length === 0) return null;
  const active = list.filter((r) => ACTIVE.includes(r.status));
  return (
    <section aria-label="Suivi des exécutions" className="run-picker">
      <h2 className="run-picker-title">Suivre une exécution{active.length > 0 && <span className="chip chip-live" role="status">{active.length} en cours</span>}</h2>
      <ul className="run-chips">
        {active.length > 1 && (
          <li><button type="button" aria-pressed={selected === ALL_ACTIVE} onClick={() => onSelect(selected === ALL_ACTIVE ? null : ALL_ACTIVE)}>Toutes les exécutions en cours ({active.length})</button></li>
        )}
        {list.slice(0, 8).map((r) => (
          <li key={r.id}>
            <button type="button" aria-pressed={selected === r.id} className={`run-chip run-chip-${r.status}`} onClick={() => onSelect(selected === r.id ? null : r.id)}>
              <StatusBadge status={r.status} label={r.cancel_requested && ACTIVE.includes(r.status) ? "Annulation" : r.status_label} /> {formatDate(r.created_at)} <span className="hint">· {r.requested_by ?? "—"} · v{r.workflow.version}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function useRun(id: string) {
  const { workspace } = useApp();
  return useQuery({
    queryKey: ["run", id], queryFn: () => api<WorkflowRun>(`/workspaces/${workspace.slug}/workflow-runs/${id}/`),
    refetchInterval: (q) => (q.state.data && ACTIVE.includes(q.state.data.status) ? 1500 : false),
  });
}

/** Progress of one execution drawn on the workflow diagram. */
export function SingleRunFollow({ id }: { id: string }) {
  const { workspace, can } = useApp();
  const qc = useQueryClient();
  const { data: run, isLoading, isError } = useRun(id);
  const cancel = useMutation({
    mutationFn: () => api(`/workspaces/${workspace.slug}/workflow-runs/${id}/cancel/`, { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["run", id] }),
  });
  if (isLoading) return <p role="status">{t("common.loading")}</p>;
  if (isError || !run) return <p role="alert">{t("common.error")}</p>;
  const active = ACTIVE.includes(run.status);
  const total = run.graph?.nodes.length ?? 0;
  const done = (run.steps ?? []).filter((s) => ["succeeded", "skipped"].includes(s.status) && s.node_type !== "compensation").length;
  return (
    <div className="run-follow">
      <div className="run-follow-head">
        <p>
          <StatusBadge status={run.status} label={run.cancel_requested && active ? "Annulation en cours" : run.status_label} /> <strong>{done}/{total} étape(s) terminée(s)</strong>
          <span className="hint"> · lancée le {formatDate(run.created_at)} par {run.requested_by ?? "—"} · version {run.workflow.version} (définition figée) · {t(`env.${run.environment}` as never)}</span>
        </p>
        <progress max={Math.max(total, 1)} value={done} aria-label="Avancement de l'exécution" />
        <div role="status" aria-live="polite">{active && <p>{t("exec.inProgress")}</p>}{run.status === "succeeded" && <p>{t("exec.succeeded")}</p>}{run.status === "failed" && <p>{t("exec.failed")}</p>}{run.status === "cancelled" && <p>L'exécution a été annulée.</p>}</div>
        <div className="actions">
          <Link className="button" to={`/runs/${run.id}`}>Détail complet (journaux, chronologie)</Link>
          {active && can("run") && !run.cancel_requested && <button type="button" onClick={() => { if (window.confirm("L'annulation arrête les étapes en cours mais n'annule pas les effets déjà réalisés. Annuler l'exécution ?")) cancel.mutate(); }}>Annuler l'exécution</button>}
        </div>
      </div>
      <RunSummary run={run} />
      <RunSchemaView run={run} />
    </div>
  );
}

/** Every active execution of the workflow at once: each step shows how many runs are currently on it. */
export function AllActiveFollow({ slug }: { slug: string }) {
  const { workspace } = useApp();
  const runs = useWorkflowRuns(slug);
  const activeIds = (runs.data?.results ?? []).filter((r) => ACTIVE.includes(r.status)).slice(0, MAX_AGGREGATED).map((r) => r.id);
  const details = useQueries({
    queries: activeIds.map((id) => ({
      queryKey: ["run", id], queryFn: () => api<WorkflowRun>(`/workspaces/${workspace.slug}/workflow-runs/${id}/`), refetchInterval: 1500,
    })),
  });
  const loaded = details.map((d) => d.data).filter((r): r is WorkflowRun => Boolean(r));
  if (activeIds.length === 0) return <p role="status">Aucune exécution en cours pour ce workflow.</p>;
  const ref = loaded[0];
  if (!ref) return <p role="status">{t("common.loading")}</p>;
  const same = loaded.filter((r) => r.workflow.version === ref.workflow.version);
  const counts: Record<string, number> = {};
  for (const r of same) for (const s of r.steps ?? []) if (BUSY_STEP.includes(s.status)) counts[s.node_id] = (counts[s.node_id] ?? 0) + 1;
  const statuses = Object.fromEntries(Object.keys(counts).map((k) => [k, "running"]));
  return (
    <div className="run-follow">
      <p role="status" aria-live="polite"><strong>{same.length} exécution(s) en cours</strong> de la version {ref.workflow.version} : le chiffre ×N sur une étape indique combien d'exécutions s'y trouvent.
        {loaded.length > same.length && ` ${loaded.length - same.length} exécution(s) d'une autre version ne sont pas comptées.`}
        {runs.data && runs.data.results.filter((r) => ACTIVE.includes(r.status)).length > MAX_AGGREGATED && ` Seules les ${MAX_AGGREGATED} plus récentes sont suivies.`}</p>
      <ul className="plain">
        {same.map((r) => {
          const at = (r.steps ?? []).filter((s) => BUSY_STEP.includes(s.status)).map((s) => s.name);
          return <li key={r.id}>{formatDate(r.created_at)} — {at.length ? `à l'étape ${at.join(", ")}` : "démarrage"} · <Link to={`/runs/${r.id}`}>détail</Link></li>;
        })}
      </ul>
      <div className="run-schema single"><CanvasView graph={runGraph(ref)} issues={[]} selectedId={null} onSelect={() => undefined} readOnly statuses={statuses} counts={counts} /></div>
    </div>
  );
}
