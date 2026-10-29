import { PageHeader } from "../components/PageHeader";
import { NotFoundPage } from "./NotFoundPage";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, type Action, type ApiError, type Execution } from "../api";
import { FavoriteStar } from "../components/FavoriteStar";
import { defaultsFor, SchemaForm, validateValues } from "../components/SchemaForm";
import { useApp } from "../context";
import { useTestCases } from "./library/TestCases";
import { t } from "../i18n";

export function ActionDetailPage() {
  const { slug = "" } = useParams();
  const { workspace, environment, can } = useApp();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const location = useLocation();
  const prefill = (location.state as { inputs?: Record<string, unknown> } | null)?.inputs;

  const { data: action, isLoading, isError } = useQuery({
    queryKey: ["action", workspace.slug, slug],
    queryFn: () => api<Action>(`/workspaces/${workspace.slug}/actions/${slug}/`),
  });
  const cases = useTestCases(slug);
  const [chosen, setChosen] = useState("");
  const version = action?.latest_version ?? null;
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (version) setValues(prefill ?? defaultsFor(version.input_schema));
  }, [version, prefill]);

  const run = useMutation({
    mutationFn: () => api<Execution>(`/workspaces/${workspace.slug}/executions/`, {
      json: { action: slug, environment, inputs: values },
    }),
    onSuccess: (ex) => {
      void qc.invalidateQueries({ queryKey: ["executions"] });
      navigate(`/executions/${ex.id}`);
    },
    onError: (e: ApiError) => {
      if (e.status === 400 && e.details) {
        setErrors(Object.fromEntries(Object.entries(e.details).map(([k, v]) => [k, String(Array.isArray(v) ? v[0] : v)])));
      } else {
        setErrors({ _: e.message });
      }
    },
  });

  if (isLoading) return <p role="status">{t("common.loading")}</p>;
  if (isError || !action || !version) return <NotFoundPage title="Action introuvable" message="Cette action n'existe pas, n'a pas de version publiée, ou vous n'avez pas le droit de la voir." />;
  const allowed = can("run");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const problems = validateValues(version.input_schema, values);
    setErrors(problems);
    if (Object.keys(problems).length === 0) run.mutate();
  };

  return (
    <>
      <nav aria-label={t("breadcrumb")} className="breadcrumb">
        <Link to="/library">Bibliothèques</Link> / <Link to={`/library/actions/${action.slug}`}>{action.name}</Link> / <span aria-current="page">Lancer un essai</span>
      </nav>
      <PageHeader title={action.name} description={action.description} actions={<FavoriteStar kind="action" slug={slug} name={action.name} />} />
      <div className="detail-grid">
        <section className="card" aria-labelledby="act-in">
          <h2 id="act-in">{t("action.inputs")}</h2>
          <form onSubmit={submit} noValidate>
            {(cases.data?.results.length ?? 0) > 0 && (
              <div className="field tc-pick">
                <label htmlFor="tc-pick">Jeux de test</label>
                <select id="tc-pick" value={chosen} onChange={(e) => {
                  const c = cases.data?.results.find((x) => String(x.id) === e.target.value);
                  setChosen(e.target.value);
                  if (c) { setValues(c.inputs); setErrors({}); }
                }}>
                  <option value="">Choisir un jeu de test…</option>
                  {cases.data?.results.map((c) => <option key={c.id} value={c.id}>{c.name}{c.valid ? "" : " (contrat modifié)"}</option>)}
                </select>
                <p className="hint">Remplit le formulaire avec des entrées enregistrées ; vous pouvez ensuite les modifier avant de lancer.</p>
              </div>
            )}
            <SchemaForm schema={version.input_schema} values={values} onChange={setValues} errors={errors} disabled={!allowed} />
            {errors._ && <p role="alert" className="field-error">Erreur : {errors._}</p>}
            <div className="actions">
              {version.examples[0] && (
                <button type="button" onClick={() => setValues(version.examples[0] ?? {})}>{t("action.example")}</button>
              )}
              <button type="submit" className="primary" disabled={!allowed || run.isPending} aria-describedby={allowed ? undefined : "no-right"}>
                {run.isPending ? t("action.running") : t("action.run")}
              </button>
            </div>
            {!allowed && <p id="no-right" className="hint">{t("action.noRunRight")}</p>}
          </form>
        </section>
        <aside className="card" aria-label="Informations sur l'action">
          <dl className="facts">
            <dt>{t("catalog.version")}</dt><dd>{version.version}</dd>
            <dt>{t("catalog.owner")}</dt><dd>{action.owner ?? "—"}</dd>
            <dt>{t("action.sideEffects")}</dt>
            <dd>{t(`action.sideEffects.${version.side_effects}` as never)}</dd>
          </dl>
          <h2>{t("action.outputs")}</h2>
          <ul>
            {Object.entries(version.output_schema.properties ?? {}).map(([k, p]) => <li key={k}><strong>{p.title ?? k}</strong>{p.description ? ` : ${p.description}` : ""}</li>)}
            {Object.keys(version.output_schema.properties ?? {}).length === 0 && <li>{version.output_schema.description || "Aucune sortie."}</li>}
          </ul>
        </aside>
      </div>
    </>
  );
}
