import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type ApiError } from "../api";
import { AiNotice, useAiMode } from "../components/AiBox";
import { PageHeader } from "../components/PageHeader";
import { useApp } from "../context";
import { formatDate, t } from "../i18n";
import { errorText } from "../lib";
import type { JsonSchema } from "../api";

interface Card { slug: string; name: string; description: string; folder: string; version: string; changelog: string; published_at: string; inputs: number }
interface Detail extends Card { input_schema: JsonSchema; has_form: boolean; can_run: boolean }

/** « Catalogue d'automatisations » : the finished workflows this space chose to offer (not the actions they are built from). */
export function CatalogPage() {
  const { workspace, can } = useApp();
  const [q, setQ] = useState("");
  const ai = useAiMode();
  const aiSearch = useMutation<{ matches: { slug: string; name: string; reason: string }[] }, ApiError, string>({
    mutationFn: (query) => api(`/workspaces/${workspace.slug}/ai/catalog-search/`, { json: { query } }),
  });
  const { data, isLoading, isError } = useQuery({
    queryKey: ["catalog", workspace.slug, q],
    queryFn: () => api<{ results: Card[] }>(`/workspaces/${workspace.slug}/catalog/?q=${encodeURIComponent(q)}`),
  });
  return (
    <>
      <PageHeader title={t("catalog.title")} description="Les automatisations prêtes à l'emploi de cet espace : choisissez-en une, renseignez ses informations et lancez-la." />
      <div className="field">
        <label htmlFor="search">{t("catalog.search")}</label>
        <input id="search" type="search" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {ai.enabled && (
        <div>
          <button type="button" onClick={() => aiSearch.mutate(q)} disabled={!q.trim() || aiSearch.isPending}>Chercher avec l'assistant IA</button>
          <AiNotice mode={ai.mode} />
          {aiSearch.error && <p role="alert" className="field-error">Erreur : {aiSearch.error.message}</p>}
          {aiSearch.data && (aiSearch.data.matches.length === 0 ? <p>L'assistant ne propose aucune automatisation pour cette demande.</p> : (
            <ul aria-label="Suggestions de l'assistant">{aiSearch.data.matches.map((m) => <li key={m.slug}><Link to={`/catalog/${m.slug}`}>{m.name}</Link> — {m.reason}</li>)}</ul>))}
        </div>
      )}
      {isLoading && <p role="status">{t("common.loading")}</p>}
      {isError && <p role="alert">{t("common.error")}</p>}
      {data && data.results.length === 0 && (
        <p>{q ? t("catalog.empty") : <>Aucune automatisation n'est encore proposée dans cet espace.{can("publish") && <> Ouvrez un workflow publié et choisissez <strong>« Publier au catalogue »</strong>.</>}</>}</p>
      )}
      <ul className="cards">
        {data?.results.map((a) => (
          <li key={a.slug} className="card">
            <h2><Link to={`/catalog/${a.slug}`}>{a.name}</Link></h2>
            <p>{a.description}</p>
            <p className="meta">
              {a.folder && <span className="tag">{a.folder}</span>}
              <span>{t("catalog.version")} {a.version}</span>
              <span>{a.inputs === 0 ? "aucune information à fournir" : `${a.inputs} information${a.inputs > 1 ? "s" : ""} à fournir`}</span>
            </p>
          </li>
        ))}
      </ul>
    </>
  );
}

export function CatalogDetailPage() {
  const { slug = "" } = useParams();
  const { workspace } = useApp();
  const { data, isLoading, error } = useQuery<Detail, ApiError>({
    queryKey: ["catalog", workspace.slug, "detail", slug],
    queryFn: () => api<Detail>(`/workspaces/${workspace.slug}/catalog/${slug}/`),
    retry: false,
  });
  if (isLoading) return <p role="status">{t("common.loading")}</p>;
  if (error || !data) return <p role="alert">{error?.status === 404 ? "Cette automatisation n'est pas (ou plus) proposée dans le catalogue." : errorText(error)} <Link to="/catalog">Retour au catalogue</Link></p>;
  const props = Object.entries(data.input_schema.properties ?? {}) as [string, JsonSchema][];
  const required = new Set((data.input_schema as { required?: string[] }).required ?? []);
  return (
    <>
      <nav aria-label={t("breadcrumb")} className="breadcrumb"><Link to="/catalog">{t("nav.catalog")}</Link> / <span aria-current="page">{data.name}</span></nav>
      <PageHeader title={data.name} description={data.description} actions={data.can_run
        ? <Link className="button primary" to={`/workflows/${data.slug}/run`}>Lancer</Link>
        : <span className="hint">Droit de lancement requis</span>} />
      <section className="card" aria-labelledby="cat-info">
        <h2 id="cat-info">Informations à fournir</h2>
        {props.length === 0 ? <p>Cette automatisation ne demande aucune information : lancez-la directement.</p> : (
          <ul>{props.map(([k, p]) => <li key={k}><strong>{p.title ?? k}</strong>{required.has(k) ? " (obligatoire)" : " (facultatif)"}{p.description ? ` — ${p.description}` : ""}</li>)}</ul>
        )}
        {data.has_form && <p className="hint">Un formulaire par étapes vous guide pour les saisir.</p>}
      </section>
      <p className="meta">{t("catalog.version")} {data.version} · publiée le {formatDate(data.published_at)}{data.changelog ? ` — ${data.changelog}` : ""}{data.folder ? ` · ${data.folder}` : ""}</p>
    </>
  );
}
