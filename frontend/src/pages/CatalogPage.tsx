import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ApiError, type WorkflowRun } from "../api";
import { defaultsFor } from "../components/SchemaForm";
import { WizardForm } from "../workflow/WizardForm";
import type { FormDef } from "../workflow/types";
import { AiNotice, useAiMode } from "../components/AiBox";
import { Avatar, Badge, SearchBox, usePaged } from "../components/AdminUi";
import { PageHeader } from "../components/PageHeader";
import { useApp } from "../context";
import { formatDate, t } from "../i18n";
import { errorText } from "../lib";
import type { JsonSchema } from "../api";
import { TagFilter, TagList } from "../components/Tags";

interface Card { slug: string; name: string; description: string; folder: string; tags?: string[]; version: string; changelog: string; published_at: string; inputs: number }
interface Detail extends Card { input_schema: JsonSchema; has_form: boolean; form: FormDef | null; can_run: boolean }

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
  const [cat, setCat] = useState("");
  const [tagF, setTagF] = useState("");
  const [sort, setSort] = useState("name");
  const [view, setView] = useState<"cards" | "list">(() => { try { return localStorage.getItem("orch.catalog.view") === "list" ? "list" : "cards"; } catch { return "cards"; } });
  useEffect(() => { try { localStorage.setItem("orch.catalog.view", view); } catch { /* preference only */ } }, [view]);
  const all = data?.results ?? [];
  const top = (c: Card) => c.folder.split("/")[0] ?? "";
  const cats = useMemo(() => {
    const m = new Map<string, number>();
    for (const c of all) m.set(top(c), (m.get(top(c)) ?? 0) + 1);
    return [...m.entries()].sort((x, y) => x[0].localeCompare(y[0], "fr"));
  }, [all]);
  const shown = useMemo(() => {
    const rows = all.filter((c) => (!cat || top(c) === (cat === "\u0000" ? "" : cat)) && (!tagF || (c.tags ?? []).includes(tagF)));
    const by: Record<string, (x: Card, y: Card) => number> = {
      name: (x, y) => x.name.localeCompare(y.name, "fr", { sensitivity: "base" }),
      recent: (x, y) => Date.parse(y.published_at) - Date.parse(x.published_at),
      simple: (x, y) => x.inputs - y.inputs || x.name.localeCompare(y.name, "fr"),
    };
    return [...rows].sort(by[sort] ?? by.name!);
  }, [all, cat, sort, tagF]);
  const paged = usePaged(shown, 12, [12, 24, 48]);
  const noFolder = "\u0000";
  return (
    <>
      <PageHeader title={t("catalog.title")} description="Les automatisations prêtes à l'emploi de cet espace : choisissez-en une, renseignez ses informations et lancez-la." />
      <section className="adm-card cat-bar">
        <div className="adm-filters">
          <SearchBox label={t("catalog.search")} value={q} onChange={setQ} placeholder="Nom, description, dossier ou étiquette" />
            <TagFilter lists={all.map((c) => c.tags)} value={tagF} onChange={setTagF} />
          <div className="field"><label htmlFor="cat-sort">Trier par</label>
            <select id="cat-sort" value={sort} onChange={(e) => setSort(e.target.value)}><option value="name">Nom (A → Z)</option><option value="recent">Publiées récemment</option><option value="simple">Les plus simples d'abord</option></select></div>
          <span style={{ flex: 1 }} />
          <span className="lst-count" role="status">{shown.length} automatisation(s)</span>
          <div className="seg" role="group" aria-label="Affichage">
            <button type="button" aria-pressed={view === "cards"} onClick={() => setView("cards")}>Cartes</button>
            <button type="button" aria-pressed={view === "list"} onClick={() => setView("list")}>Liste</button>
          </div>
        </div>
        {cats.length > 1 && (
          <div className="cat-chips" role="group" aria-label="Catégories">
            <button type="button" className="chip-f" aria-pressed={cat === ""} onClick={() => setCat("")}>Toutes <span>{all.length}</span></button>
            {cats.map(([c, n]) => <button type="button" key={c || noFolder} className="chip-f" aria-pressed={cat === (c || noFolder)} onClick={() => setCat(c || noFolder)}>{c || "Sans dossier"} <span>{n}</span></button>)}
          </div>
        )}
      </section>
      {ai.enabled && (
        <section className="adm-card">
          <button type="button" onClick={() => aiSearch.mutate(q)} disabled={!q.trim() || aiSearch.isPending}>Chercher avec l'assistant IA</button>
          <AiNotice mode={ai.mode} />
          {aiSearch.error && <p role="alert" className="field-error">Erreur : {aiSearch.error.message}</p>}
          {aiSearch.data && (aiSearch.data.matches.length === 0 ? <p>L'assistant ne propose aucune automatisation pour cette demande.</p> : (
            <ul aria-label="Suggestions de l'assistant">{aiSearch.data.matches.map((m) => <li key={m.slug}><Link to={`/catalog/${m.slug}`}>{m.name}</Link> — {m.reason}</li>)}</ul>))}
        </section>
      )}
      {isLoading && <p role="status">{t("common.loading")}</p>}
      {isError && <p role="alert">{t("common.error")}</p>}
      {data && shown.length === 0 && (
        <div className="ops-empty">
          <strong>{q || cat ? t("catalog.empty") : "Le catalogue est vide"}</strong>
          {!q && !cat && <p className="hint">Aucune automatisation n'est encore proposée dans cet espace.{can("publish") && <> Ouvrez un workflow publié et choisissez <strong>« Publier au catalogue »</strong>.</>}</p>}
        </div>
      )}
      {view === "cards" ? (
        <ul className="cards cat-cards">
          {paged.slice.map((a) => (
            <li key={a.slug} className="card cat-card">
              <div className="cat-head"><span className="cat-av"><Avatar name={a.name} /></span><h2><Link to={`/catalog/${a.slug}`}>{a.name}</Link></h2></div>
              <p className="cat-desc">{a.description}</p>
              {(a.tags?.length ?? 0) > 0 && <p className="cat-tags"><TagList tags={a.tags} onPick={setTagF} active={tagF} /></p>}
              <p className="meta">
                {a.folder && <span className="tag">{a.folder}</span>}
                <Badge tone="info">{t("catalog.version")} {a.version}</Badge>
                <span>{a.inputs === 0 ? "aucune information à fournir" : `${a.inputs} information${a.inputs > 1 ? "s" : ""} à fournir`}</span>
              </p>
              <p className="cat-foot"><span className="hint">Publiée le {formatDate(a.published_at)}</span><span className="button primary" aria-hidden="true">Ouvrir</span></p>
            </li>
          ))}
        </ul>
      ) : shown.length > 0 && (
        <section className="adm-card">
          <div className="table-wrap"><table className="adm-table"><caption className="sr-only">Catalogue d'automatisations</caption>
            <thead><tr><th scope="col">Automatisation</th><th scope="col">Dossier</th><th scope="col">Version</th><th scope="col">Informations</th><th scope="col">Publiée le</th></tr></thead>
            <tbody>{paged.slice.map((a) => (
              <tr key={a.slug}>
                <td><span className="adm-who"><Avatar name={a.name} /><span><Link className="lst-title" to={`/catalog/${a.slug}`}>{a.name}</Link>{a.description && <span className="lst-sub">{a.description}</span>}<TagList tags={a.tags} onPick={setTagF} active={tagF} /></span></span></td>
                <td>{a.folder ? <span className="tag">{a.folder}</span> : "—"}</td><td><Badge tone="info">{a.version}</Badge></td><td>{a.inputs}</td><td>{formatDate(a.published_at)}</td>
              </tr>))}</tbody></table></div>
        </section>
      )}
      {paged.pager("Pagination du catalogue")}
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
  return <CatalogRun data={data} />;
}

/** The sheet of a catalog entry: what it does and the form to fill in, right here (the run starts from this page). */
function CatalogRun({ data }: { data: Detail }) {
  const { workspace, environment } = useApp();
  const navigate = useNavigate();
  const [values, setValues] = useState<Record<string, unknown>>(() => defaultsFor(data.input_schema));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const run = useMutation({
    mutationFn: () => api<WorkflowRun>(`/workspaces/${workspace.slug}/workflows/${data.slug}/runs/`, { json: { environment, inputs: values } }),
    onSuccess: (r) => navigate(`/workflows/${data.slug}/follow?run=${r.id}`),
    onError: (e: ApiError) => setErrors(e.status === 400 && e.details ? Object.fromEntries(Object.entries(e.details).map(([k, v]) => [k, String(Array.isArray(v) ? v[0] : v)])) : { _: e.message }),
  });
  const none = Object.keys(data.input_schema.properties ?? {}).length === 0;
  return (
    <>
      <nav aria-label={t("breadcrumb")} className="breadcrumb"><Link to="/catalog">{t("nav.catalog")}</Link> / <span aria-current="page">{data.name}</span></nav>
      <PageHeader title={data.name} description={data.description} />
      <p className="hint">Environnement : <strong>{t(`env.${environment}` as never)}</strong></p>
      {none && <p>Cette automatisation ne demande aucune information : lancez-la directement.</p>}
      <WizardForm schema={data.input_schema} form={data.form} values={values} onChange={setValues} errors={errors} disabled={!data.can_run} submitLabel={t("action.run")}
        busy={run.isPending} onSubmit={() => run.mutate()} />
      {!data.can_run && <p id="no-run" className="hint">{t("action.noRunRight")}</p>}
      <p className="meta">{t("catalog.version")} {data.version} · publiée le {formatDate(data.published_at)}{data.changelog ? ` — ${data.changelog}` : ""}{data.folder ? ` · ${data.folder}` : ""}</p>
    </>
  );
}
