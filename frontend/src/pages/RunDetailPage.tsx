import { NotFoundPage } from "./NotFoundPage";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ApiError, type StepRun, type WorkflowRun } from "../api";
import { AiNotice, useAiMode } from "../components/AiBox";
import { Fields } from "../components/Fields";
import { Tabs } from "../components/Tabs";
import { VariableBlock } from "../workflow/RunInspector";
import { useFitHeight } from "../workflow/useFitHeight";
import { RunSchemaView, RunSummary } from "../workflow/RunSchemaView";
import { StatusBadge } from "../components/StatusBadge";
import { useApp } from "../context";
import { formatDate, t } from "../i18n";
import { HelpTip } from "../components/HelpTip";

const ACTIVE = ["running", "compensating"];

function UncertainBox({ step, runId, slug }: { step: StepRun; runId: string; slug: string }) {
  const qc = useQueryClient();
  const [choice, setChoice] = useState("");
  const decide = useMutation({
    mutationFn: () => api(`/workspaces/${slug}/workflow-runs/${runId}/steps/${step.node_id}/decision/`, { json: { decision: choice, output: choice === "succeed" ? {} : undefined } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["run", runId] }),
  });
  return (
    <form className="error-box" onSubmit={(e) => { e.preventDefault(); if (choice) decide.mutate(); }}>
      <h3>Décision requise : l'issue de « {step.name} » est inconnue</h3>
      <p>{step.error?.message} {step.error?.next_step}</p>
      <fieldset>
        <legend>Que voulez-vous faire ?</legend>
        <div className="field field-inline"><input type="radio" id={`d-s-${step.node_id}`} name={`d-${step.node_id}`} value="succeed" onChange={(e) => setChoice(e.target.value)} /><label htmlFor={`d-s-${step.node_id}`}>J'ai vérifié : l'effet a bien eu lieu (marquer comme réussie)</label></div>
        <div className="field field-inline"><input type="radio" id={`d-r-${step.node_id}`} name={`d-${step.node_id}`} value="retry" onChange={(e) => setChoice(e.target.value)} /><label htmlFor={`d-r-${step.node_id}`}>J'ai vérifié : l'effet n'a pas eu lieu (relancer l'étape)</label></div>
        <div className="field field-inline"><input type="radio" id={`d-f-${step.node_id}`} name={`d-${step.node_id}`} value="fail" onChange={(e) => setChoice(e.target.value)} /><label htmlFor={`d-f-${step.node_id}`}>Abandonner l'étape (le workflow échouera)</label></div>
      </fieldset>
      <button type="submit" className="primary" disabled={!choice || decide.isPending}>Confirmer ma décision</button>
    </form>
  );
}

export function RunDetailPage() {
  const { id = "" } = useParams();
  const { workspace, can } = useApp();
  const qc = useQueryClient();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const ai = useAiMode();
  const navigate = useNavigate();
  const rerun = useMutation<{ id: string }, ApiError, boolean>({
    mutationFn: (fromFailed) => api(`/workspaces/${workspace.slug}/workflow-runs/${id}/rerun/`, { json: { from_failed: fromFailed } }),
    onSuccess: (r) => navigate(`/runs/${r.id}`),
  });
  const explain = useMutation<{ summary: string; likely_causes: string[]; next_steps: string[]; fix_request?: string; workflow: string; notice: string }, ApiError>({
    mutationFn: () => api(`/workspaces/${workspace.slug}/ai/explain-error/`, { json: { run_id: id } }),
  });
  const { data: run, isLoading, isError } = useQuery({
    queryKey: ["run", id],
    queryFn: () => api<WorkflowRun>(`/workspaces/${workspace.slug}/workflow-runs/${id}/`),
    refetchInterval: (q) => (q.state.data && ACTIVE.includes(q.state.data.status) ? 1500 : false),
  });
  const cancel = useMutation({
    mutationFn: () => api(`/workspaces/${workspace.slug}/workflow-runs/${id}/cancel/`, { method: "POST" }),
    onSuccess: () => { setConfirmCancel(false); void qc.invalidateQueries({ queryKey: ["run", id] }); },
  });
  if (isLoading) return <p role="status">{t("common.loading")}</p>;
  if (isError || !run) return <NotFoundPage title="Exécution introuvable" message="Cette exécution n'existe pas ou vous n'avez pas le droit de la voir." />;
  const active = ACTIVE.includes(run.status);
  const explainPanel = (
    <>
        {run.status === "failed" && ai.enabled && (
          <section aria-labelledby="ex" className="card">
            <h2 id="ex">Comprendre l'erreur avec l'assistant</h2>
            <button type="button" onClick={() => explain.mutate()} disabled={explain.isPending}>Expliquer l'erreur</button>
            <AiNotice mode={ai.mode} />
            {explain.error && <p role="alert" className="field-error">Erreur : {explain.error.message}</p>}
            {explain.data && <div role="status"><p>{explain.data.summary}</p><h3>Causes possibles</h3><ul>{explain.data.likely_causes.map((c) => <li key={c}>{c}</li>)}</ul><h3>Pistes</h3><ul>{explain.data.next_steps.map((c) => <li key={c}>{c}</li>)}</ul>{explain.data.fix_request && <><h3>Correction proposée</h3><p>« {explain.data.fix_request} »</p><p><Link className="button primary" to={`/workflows/${explain.data.workflow}/schema?assist=${encodeURIComponent(explain.data.fix_request)}`}>Proposer cette correction dans l'éditeur</Link> <span className="hint">(l'assistant prépare des modifications du brouillon ; vous les relisez avant de les appliquer)</span></p></>}<p className="hint">{explain.data.notice}</p></div>}
          </section>
        )}
    </>
  );
  return (
    <>
      <nav aria-label={t("breadcrumb")} className="breadcrumb"><Link to="/executions">{t("nav.executions")}</Link> / <span aria-current="page">{run.workflow.name}</span></nav>
      <h1>{run.workflow.name} <small>v{run.workflow.version}</small></h1>
      <p>{t("exec.status")} : <StatusBadge status={run.status} label={run.cancel_requested && active ? "Annulation en cours" : run.status_label} /> · Environnement : {t(`env.${run.environment}` as never)}</p>
      <div role="status" aria-live="polite">{active && <p>{t("exec.inProgress")}</p>}{run.status === "succeeded" && <p>{t("exec.succeeded")}</p>}{run.status === "failed" && <p>{t("exec.failed")}</p>}{run.status === "cancelled" && <p>L'exécution a été annulée.</p>}</div>
      {active && can("run") && !run.cancel_requested && (
        confirmCancel ? (
          <p role="alert">L'annulation arrête les étapes en cours mais n'annule pas les effets déjà réalisés. <button type="button" className="danger" onClick={() => cancel.mutate()}>Oui, annuler l'exécution</button> <button type="button" onClick={() => setConfirmCancel(false)}>Non</button></p>
        ) : <button type="button" onClick={() => setConfirmCancel(true)}>Annuler l'exécution</button>
      )}
      {!active && can("run") && (
        <p>{run.status === "failed" && <><button type="button" className="primary" onClick={() => rerun.mutate(true)} disabled={rerun.isPending}>Reprendre à l'étape en échec</button> </>}<button type="button" onClick={() => rerun.mutate(false)} disabled={rerun.isPending}>Relancer depuis le début avec les mêmes entrées</button>{rerun.error && <span role="alert" className="field-error"> Erreur : {rerun.error.message}</span>}</p>
      )}
      {run.steps?.filter((s) => s.resume_url).map((s) => (
        <section key={s.node_id} className="card" aria-label={`Reprise de « ${s.name} »`}>
          <h2>« {s.name} » {s.resume_url?.startsWith("/r/") ? "attend une réponse à un formulaire" : "attend un appel HTTP"}</h2>
          <p>{s.resume_url?.startsWith("/r/") ? "Transmettez cette adresse à la personne qui doit répondre (une seule réponse) :" : "Envoyez un POST JSON à cette adresse pour reprendre l'exécution (une seule fois) :"}</p>
          <p><code>{`${window.location.origin}${s.resume_url}`}</code></p>
          {s.available_at && <p className="hint">Sans appel, l'exécution reprend le {formatDate(s.available_at)} avec <code>resumed = false</code>. Cette adresse est un secret : ne la partagez qu'avec le système censé reprendre.</p>}
        </section>
      ))}
      {run.steps?.filter((s) => s.status === "uncertain").map((s) => can("run") ? <UncertainBox key={s.node_id} step={s} runId={run.id} slug={workspace.slug} /> : null)}
      <RunTabs run={run} explain={explainPanel} />
      {run.runtime?.image && <p className="hint">Environnement d'exécution figé : {run.runtime.image}{run.runtime.digest ? ` (${run.runtime.digest.slice(0, 19)}…)` : ""}</p>}
    </>
  );
}


interface RunFileRow { id: string; step_id: string; name: string; size: number; content_type: string; sha256: string }

function sizeLabel(n: number): string { return n < 1024 ? `${n} o` : n < 1048576 ? `${(n / 1024).toFixed(1)} Ko` : `${(n / 1048576).toFixed(1)} Mo`; }

function RunFiles({ rows, slug }: { rows: RunFileRow[]; slug: string }) {
  return (
    <>
      <p className="help-row"><HelpTip caption="À savoir">Fichiers produits par les étapes de cette exécution ; ils sont supprimés avec elle (durée de conservation de l'espace de travail).</HelpTip></p>
      <table>
        <caption className="sr-only">Fichiers de l'exécution</caption>
        <thead><tr><th scope="col">Nom</th><th scope="col">Étape</th><th scope="col">Type</th><th scope="col">Taille</th><th scope="col">Téléchargement</th></tr></thead>
        <tbody>{rows.map((f) => (
          <tr key={f.id}><th scope="row">{f.name}</th><td>{f.step_id}</td><td>{f.content_type}</td><td>{sizeLabel(f.size)}</td>
            <td><a href={`/api/v1/workspaces/${slug}/run-files/${f.id}/download/`} download={f.name} aria-label={`Télécharger ${f.name}`}>Télécharger</a></td></tr>
        ))}</tbody>
      </table>
    </>
  );
}

/** A bordered block that fits between its top and the bottom of the window: its content scrolls inside it, not the page. */
function ScrollBlock({ label, children }: { label: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const h = useFitHeight(ref, 240);
  return <div ref={ref} className="scroll-block" role="region" aria-label={label} tabIndex={0} style={h ? { maxHeight: h } : undefined}>{children}</div>;
}

function duration(a: string, b: string | null): string {
  if (!b) return "—";
  const ms = new Date(b).getTime() - new Date(a).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  return ms < 1000 ? `${ms} ms` : ms < 60000 ? `${(ms / 1000).toFixed(1).replace(".", ",")} s` : `${Math.floor(ms / 60000)} min ${Math.round((ms % 60000) / 1000)} s`;
}

const NODE_TYPE_LABEL: Record<string, string> = { action: "Action", script: "Script", condition: "Condition", switch: "Aiguillage", join: "Jonction", approval: "Approbation", wait: "Attente", ai: "IA", http: "HTTP", agent: "Agent", foreach: "Boucle", subworkflow: "Sous-workflow", transform: "Transformation", compensation: "Compensation" };

function RunSteps({ steps }: { steps: StepRun[] }) {
  if (steps.length === 0) return <p className="hint">Aucune étape n'a encore démarré.</p>;
  return (
    <table className="run-steps">
        <caption className="sr-only">Étapes de l'exécution</caption>
        <thead><tr><th scope="col">Étape</th><th scope="col">Statut</th><th scope="col">Début</th><th scope="col">Durée</th><th scope="col">Détail</th></tr></thead>
        <tbody>
          {steps.map((s) => (
            <tr key={s.node_id} className={`rs-${s.status}`}>
              <th scope="row"><strong>{s.name}</strong><br /><span className="hint"><code>{s.node_id}</code> · {NODE_TYPE_LABEL[s.node_type] ?? s.node_type}</span></th>
              <td><StatusBadge status={s.status} label={s.status_label} />{s.attempt > 1 && <><br /><span className="hint">{s.attempt} tentatives</span></>}</td>
              <td><time dateTime={s.started_at}>{formatDate(s.started_at)}</time></td>
              <td>{duration(s.started_at, s.finished_at)}</td>
              <td>
                {s.error && <p className="field-error">{s.error.message}{s.error.next_step ? ` — ${s.error.next_step}` : ""}</p>}
                {s.status === "waiting" && s.available_at && <p className="hint">Reprise prévue le {formatDate(s.available_at)}.</p>}
                {s.child_run_ids.length > 0 && <p>{s.child_run_ids.map((c, i) => <span key={c}><Link to={`/runs/${c}`}>Itération / sous-exécution {i + 1}</Link>{" "}</span>)}</p>}
                {s.output && s.status === "succeeded" && Object.keys(s.output).length > 0 && ["action", "script", "ai"].includes(s.node_type) && <details><summary>Résultat de l'étape</summary><Fields data={s.output} /></details>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
  );
}

const EVENT_TONE: Record<string, "ok" | "err" | "warn"> = {
  succeeded: "ok", step_succeeded: "ok", compensation_done: "ok",
  failed: "err", step_failed: "err", failing: "err", cancelled: "err", step_cancelled: "err",
  step_uncertain: "warn", cancel_requested: "warn", approval_requested: "warn", decision: "warn", compensation_started: "warn",
};
const EVENT_LABEL: Record<string, string> = {
  started: "Démarrage", step_started: "Étape démarrée", step_succeeded: "Étape réussie", step_failed: "Étape échouée", step_skipped: "Étape ignorée", step_cancelled: "Étape annulée",
  step_uncertain: "Issue incertaine", succeeded: "Terminée", failed: "Échec", failing: "Échec en cours", cancelled: "Annulée", cancel_requested: "Annulation demandée",
  approval_requested: "Approbation demandée", decision: "Décision", compensation_started: "Compensation", compensation_done: "Compensation terminée", http_queued: "Appel HTTP", resumed_from_failed: "Reprise",
};

function RunTimeline({ run }: { run: WorkflowRun }) {
  const events = run.events ?? [];
  const first = events[0];
  if (!first) return <p className="hint">Aucun événement.</p>;
  const names = Object.fromEntries((run.graph?.nodes ?? []).map((n) => [n.id, n.name]));
  return (
    <ol className="run-timeline" aria-label="Chronologie de l'exécution">
      {events.map((e) => (
        <li key={e.seq} className={`rt-${EVENT_TONE[e.type] ?? "info"}`}>
          <span className="rt-dot" aria-hidden="true" />
          <div className="rt-head">
            <strong>{EVENT_LABEL[e.type] ?? e.type}</strong>
            {e.node_id && <span className="chip">{names[e.node_id] ?? e.node_id}</span>}
            <span className="hint"><time dateTime={e.created_at}>{formatDate(e.created_at)}</time> · +{duration(first.created_at, e.created_at)}</span>
          </div>
          <p>{e.message}</p>
        </li>
      ))}
    </ol>
  );
}

function RunFacts({ run }: { run: WorkflowRun }) {
  return (
    <dl className="facts">
      <div className="fact"><dt>Lancée le</dt><dd>{formatDate(run.created_at)}</dd></div>
      <div className="fact"><dt>Terminée le</dt><dd>{run.finished_at ? formatDate(run.finished_at) : "—"}</dd></div>
      <div className="fact"><dt>Lancée par</dt><dd>{run.requested_by ?? "—"}</dd></div>
      <div className="fact"><dt>Version</dt><dd>{run.workflow.version} (définition figée)</dd></div>
      <div className="fact"><dt>Environnement</dt><dd>{t(`env.${run.environment}` as never)}</dd></div>
      <div className="fact"><dt>Étapes</dt><dd>{(run.steps ?? []).filter((x) => x.status === "succeeded").length} réussie(s) sur {run.graph?.nodes.length ?? (run.steps ?? []).length}</dd></div>
    </dl>
  );
}

function RunTabs({ run, explain }: { run: WorkflowRun; explain: ReactNode }) {
  const [tab, setTab] = useState("summary");
  const { workspace } = useApp();
  const files = useQuery({ queryKey: ["run-files", run.id, run.status], queryFn: () => api<RunFileRow[]>(`/workspaces/${workspace.slug}/workflow-runs/${run.id}/files/`) });
  const steps = run.steps ?? [];
  const logs = steps.filter((s) => s.logs);
  return (
    <Tabs label="Détail de l'exécution" className="run-tabs" active={tab} onChange={setTab} tabs={[
      { id: "summary", label: "Résumé", content: <><RunFacts run={run} /><RunSummary run={run} />{explain}</> },
      { id: "schema", label: "Graphe", content: <RunSchemaView run={run} fill /> },
      { id: "steps", label: `Étapes (${steps.length})`, content: <ScrollBlock label="Étapes de l'exécution"><RunSteps steps={steps} /></ScrollBlock> },
      ...((files.data?.length ?? 0) > 0 ? [{ id: "files", label: `Fichiers (${files.data!.length})`, content: <RunFiles rows={files.data!} slug={workspace.slug} /> }] : []),
      { id: "logs", label: "Journaux", content: logs.length === 0 ? <p className="hint">Aucun journal : les étapes n'ont rien écrit (utilisez <code>System.log</code> dans un script).</p> : <>{logs.map((s) => <section key={s.node_id}><h3>{s.name}</h3><pre tabIndex={0} aria-label={`Journal de ${s.name}`}>{s.logs}</pre></section>)}</> },
      { id: "io", label: "Entrées/Sorties", content: (
        <div className="run-io-tab">
          <section className="scroll-block io-constants" aria-label="Constantes du workflow">
            <VariableBlock title="Constantes" data={run.inspector?.start.constants} empty="Ce workflow n'a pas de constante." />
          </section>
          <ScrollBlock label="Entrées et sorties de l'exécution">
            <div className="inspector-grid run-io">
              <VariableBlock title="Entrées utilisées" data={run.inspector?.start.inputs} empty="Ce workflow n'a pas d'entrée." />
              <VariableBlock title="Sorties" data={run.inspector?.end.outputs} empty={run.status === "succeeded" ? "Ce workflow ne déclare aucune sortie." : "Pas de sortie pour le moment."} />
            </div>
            <p className="hint">Les mots de passe et secrets sont masqués.</p>
          </ScrollBlock>
        </div>
      ) },
      { id: "events", label: `Chronologie (${run.events?.length ?? 0})`, content: <ScrollBlock label="Chronologie de l'exécution"><RunTimeline run={run} /></ScrollBlock> },
    ]} />
  );
}
