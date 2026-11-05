import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ApiError, type WorkflowDetail, type WorkflowRun } from "../api";
import { defaultsFor } from "../components/SchemaForm";
import { FavoriteStar } from "../components/FavoriteStar";
import { WizardForm } from "../workflow/WizardForm";
import { RunForm } from "../forms/RunForm";
import { useApp } from "../context";
import { errorText, useWsMutation, useWsQuery } from "../lib";
import { t } from "../i18n";

interface PromoRow { workflow_slug: string; version: string; environment: string; status: string; requested_by: string | null }

function PromotionPanel({ slug, version }: { slug: string; version: string }) {
  const { can } = useApp();
  const q = useWsQuery<{ results: PromoRow[] }>("promotions", "/promotions/");
  const request = useWsMutation<{ environment: string; comment: string }>("POST", `/workflows/${slug}/versions/${version}/promote/`, ["promotions"]);
  const mine = (q.data?.results ?? []).filter((p) => p.workflow_slug === slug && p.version === version && p.environment === "prod");
  const approved = mine.some((p) => p.status === "approved");
  if (!q.isSuccess) return <section aria-labelledby="promo" className="notice"><h2 id="promo">Production</h2><p role="status">Vérification de l'état de promotion…</p></section>;
  const pending = mine.find((p) => p.status === "requested");
  return (
    <section aria-labelledby="promo" className="notice">
      <h2 id="promo">Production</h2>
      {approved ? <p>✔ La version {version} est promue en production.</p> : pending ? <p>⏳ Promotion demandée par {pending.requested_by} : en attente de revue par une autre personne.</p> : (
        <>
          <p>La version {version} n'est pas encore promue en production : elle ne peut pas s'y exécuter.</p>
          {can("publish") && <button type="button" onClick={() => request.mutate({ environment: "prod", comment: "" })} disabled={request.isPending}>Demander la promotion en production</button>}
        </>
      )}
      {request.error && <p role="alert" className="field-error">Erreur : {errorText(request.error)}</p>}
    </section>
  );
}

export function WorkflowRunPage() {
  const { slug = "" } = useParams();
  const { workspace, environment, can } = useApp();
  const navigate = useNavigate();
  const { data: wf, isLoading, isError } = useQuery({ queryKey: ["workflow", workspace.slug, slug], queryFn: () => api<WorkflowDetail>(`/workspaces/${workspace.slug}/workflows/${slug}/`) });
  const schema = wf?.latest_version?.input_schema;
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  useEffect(() => { if (schema) setValues(defaultsFor(schema)); }, [schema]);

  const run = useMutation({
    mutationFn: (inputs: Record<string, unknown>) => api<WorkflowRun>(`/workspaces/${workspace.slug}/workflows/${slug}/runs/`, { json: { environment, inputs } }),
    onSuccess: (r) => navigate(`/workflows/${slug}/follow?run=${r.id}`),
    onError: (e: ApiError) => setErrors(e.status === 400 && e.details ? Object.fromEntries(Object.entries(e.details).map(([k, v]) => [k, String(Array.isArray(v) ? v[0] : v)])) : { _: e.message }),
  });

  if (isLoading) return <p role="status">{t("common.loading")}</p>;
  if (isError || !wf) return <p role="alert">{t("common.error")}</p>;
  if (!wf.latest_version || !schema) return <p>Ce workflow n'a pas encore de version publiée. <Link to={`/workflows/${slug}/schema`}>Ouvrir l'éditeur</Link></p>;
  const allowed = can("run");
  return (
    <>
      <nav aria-label={t("breadcrumb")} className="breadcrumb"><Link to="/workflows">Workflows</Link> / <Link to={`/workflows/${slug}`}>{wf.name}</Link> / <span aria-current="page">Lancer</span></nav>
      <div className="wf-title"><h1>{wf.name} <small>v{wf.latest_version.version}</small></h1><FavoriteStar kind="workflow" slug={slug} name={wf.name} /></div>
      <p>{wf.description}</p>
      <p className="hint">Environnement : <strong>{t(`env.${environment}` as never)}</strong></p>
      {environment === "prod" && <PromotionPanel slug={slug} version={wf.latest_version.version} />}
      {Object.keys(schema.properties ?? {}).length === 0 && <p>Ce workflow n'a pas d'entrée : vous pouvez le lancer directement.</p>}
      {wf.latest_version.input_form ? (
        <RunForm schema={schema} definition={wf.latest_version.input_form} workspace={workspace.slug} workflow={slug} environment={environment} values={values} onChange={setValues} errors={errors} setErrors={setErrors}
          disabled={!allowed} submitLabel={t("action.run")} busy={run.isPending} onSubmit={(inputs) => run.mutate(inputs)} onCancel={() => navigate(`/workflows/${slug}`)} />
      ) : (
        <WizardForm schema={schema} form={wf.latest_version.form} values={values} onChange={setValues} errors={errors} disabled={!allowed} submitLabel={t("action.run")}
          busy={run.isPending} onSubmit={() => run.mutate(values)} onCancel={() => navigate(`/workflows/${slug}`)} />
      )}
      {!allowed && <p id="no-run" className="hint">{t("action.noRunRight")}</p>}
    </>
  );
}
