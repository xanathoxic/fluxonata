import { NotFoundPage } from "./NotFoundPage";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ApiError, type StepRun, type WorkflowRun } from "../api";
import { AiNotice, useAiMode } from "../components/AiBox";
import { Fields } from "../components/Fields";
import { Tabs } from "../components/Tabs";
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
      <RunSummary run={run} />
      {run.status === "failed" && ai.enabled && (
        <section aria-labelledby="ex" className="card">
          <h2 id="ex">Comprendre l'erreur avec l'assistant</h2>
          <button type="button" onClick={() => explain.mutate()} disabled={explain.isPending}>Expliquer l'erreur</button>
          <AiNotice mode={ai.mode} />
          {explain.error && <p role="alert" className="field-error">Erreur : {explain.error.message}</p>}
          {explain.data && <div role="status"><p>{explain.data.summary}</p><h3>Causes possibles</h3><ul>{explain.data.likely_causes.map((c) => <li key={c}>{c}</li>)}</ul><h3>Pistes</h3><ul>{explain.data.next_steps.map((c) => <li key={c}>{c}</li>)}</ul>{explain.data.fix_request && <><h3>Correction proposée</h3><p>« {explain.data.fix_request} »</p><p><Link className="button primary" to={`/workflows/${explain.data.workflow}/schema?assist=${encodeURIComponent(explain.data.fix_request)}`}>Proposer cette correction dans l'éditeur</Link> <span className="hint">(l'assistant prépare des modifications du brouillon ; vous les relisez avant de les appliquer)</span></p></>}<p className="hint">{explain.data.notice}</p></div>}
        </section>
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
      {run.status === "succeeded" && run.outputs && Object.keys(run.outputs).length > 0 && <section aria-labelledby="o"><h2 id="o">{t("exec.result")}</h2><Fields data={run.outputs} /></section>}
      <RunTabs run={run} />
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

function RunTabs({ run }: { run: WorkflowRun }) {
  const [tab, setTab] = useState("schema");
  const { workspace } = useApp();
  const files = useQuery({ queryKey: ["run-files", run.id, run.status], queryFn: () => api<RunFileRow[]>(`/workspaces/${workspace.slug}/workflow-runs/${run.id}/files/`) });
  const steps = run.steps ?? [];
  const logs = steps.filter((s) => s.logs);
  return (
    <Tabs label="Détail de l'exécution" active={tab} onChange={setTab} tabs={[
      { id: "schema", label: "Schéma", content: <RunSchemaView run={run} /> },
      { id: "steps", label: "Étapes", content: (
        <ol className="steps">
          {steps.map((s) => (
            <li key={s.node_id}>
              <StatusBadge status={s.status} label={s.status_label} /> <strong>{s.name}</strong>
              {s.attempt > 1 && <span className="hint"> · {s.attempt} tentatives</span>}
              {s.error && <p className="field-error">{s.error.message}{s.error.next_step ? ` — ${s.error.next_step}` : ""}</p>}
              {s.status === "waiting" && s.available_at && <p className="hint">Reprise prévue le {formatDate(s.available_at)}.</p>}
              {s.child_run_ids.length > 0 && <p>{s.child_run_ids.map((c, i) => <span key={c}><Link to={`/runs/${c}`}>Itération / sous-exécution {i + 1}</Link>{" "}</span>)}</p>}
              {s.output && s.status === "succeeded" && ["action", "script", "ai"].includes(s.node_type) && <details><summary>Résultat de l'étape</summary><Fields data={s.output} /></details>}
            </li>
          ))}
        </ol>
      ) },
      ...((files.data?.length ?? 0) > 0 ? [{ id: "files", label: `Fichiers (${files.data!.length})`, content: <RunFiles rows={files.data!} slug={workspace.slug} /> }] : []),
      { id: "logs", label: "Journaux", content: logs.length === 0 ? <p className="hint">Aucun journal : les étapes n'ont rien écrit (utilisez <code>System.log</code> dans un script).</p> : <>{logs.map((s) => <section key={s.node_id}><h3>{s.name}</h3><pre tabIndex={0} aria-label={`Journal de ${s.name}`}>{s.logs}</pre></section>)}</> },
      { id: "io", label: "Entrées/Sorties", content: <><h3>Entrées utilisées</h3><Fields data={run.inputs} /><h3>Sorties</h3>{run.outputs ? <Fields data={run.outputs} /> : <p className="hint">Pas encore de sortie.</p>}</> },
      { id: "events", label: "Chronologie", content: <ol className="timeline">{run.events?.map((e) => <li key={e.seq}><time dateTime={e.created_at}>{formatDate(e.created_at)}</time> — {e.message}</li>)}</ol> },
    ]} />
  );
}
