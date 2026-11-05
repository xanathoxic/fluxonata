import { FolderTree, inFolder } from "../components/FolderTree";
import { RowIcon } from "../components/RowIcon";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, apiAll, type ApiError, type WorkflowSummary } from "../api";
import { TemplatesDialog } from "./TemplatesDialog";
import { useAiMode } from "../components/AiBox";
import { AiDraftFlow } from "../components/AiDraftFlow";
import { FavoriteStar } from "../components/FavoriteStar";
import { Badge, EmptyRow, SearchBox, SortTh, usePaged, useSort } from "../components/AdminUi";
import { Overlay } from "../components/Overlay";
import { PageHeader } from "../components/PageHeader";
import { useApp } from "../context";
import { formatDate } from "../i18n";
import { TagFilter, TagInput, TagList } from "../components/Tags";

const slugify = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 50);


export function WorkflowsPage() {
  const { workspace, can } = useApp();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [name, setName] = useState("");
  const [folderNew, setFolderNew] = useState("");
  const [creating, setCreating] = useState(false);
  const [folder, setFolder] = useState("");
  const [q, setQ] = useState("");
  const [tagF, setTagF] = useState("");
  const [newTags, setNewTags] = useState<string[]>([]);
  const ai = useAiMode();
  const [aiOpen, setAiOpen] = useState(false);
  const { data, isLoading, isError } = useQuery({ queryKey: ["workflows", workspace.slug], refetchOnMount: "always", queryFn: () => apiAll<WorkflowSummary>(`/workspaces/${workspace.slug}/workflows/`) });
  const create = useMutation({
    mutationFn: () => api<WorkflowSummary>(`/workspaces/${workspace.slug}/workflows/`, { json: { name, slug: slugify(name) || "workflow", folder: folderNew || folder, tags: newTags } }),
    onSuccess: (w) => { void qc.invalidateQueries({ queryKey: ["workflows"] }); navigate(`/workflows/${w.slug}/schema`); },
  });
  const all = data?.results ?? [];
  const folders = useMemo(() => all.map((w) => w.folder), [all]);
  const filtered = useMemo(() => all.filter((w) => inFolder(w.folder, folder) && (!tagF || (w.tags ?? []).includes(tagF)) && (!q || `${w.name} ${w.description} ${(w.tags ?? []).join(" ")}`.toLowerCase().includes(q.toLowerCase()))), [all, folder, q, tagF]);
  const { sorted, sort, toggle } = useSort(filtered, {
    name: (w) => w.name, folder: (w) => w.folder, status: (w) => (w.latest_version ? 1 : 0), updated: (w) => Date.parse(w.updated_at) || 0,
  }, { key: "updated", dir: "desc" });
  const paged = usePaged(sorted, 25);
  const err = create.error as ApiError | null;
  const published = all.filter((w) => w.latest_version).length;
  return (
    <>
      <PageHeader title="Workflows" description={`${all.length} workflow(s) dans cet espace : ${published} publié(s), ${all.length - published} en brouillon.`} />
      {templatesOpen && <TemplatesDialog onClose={() => setTemplatesOpen(false)} />}
      <div className="wf-list-layout">
        <FolderTree label="Dossiers de workflows" kind="workflow" canEdit={can("edit")} folders={folders} current={folder} onPick={setFolder} />
        <section className="adm-card lst-main">
          <div className="adm-filters">
            <SearchBox label="Rechercher un workflow" value={q} onChange={setQ} placeholder="Nom, description ou étiquette" />
            <TagFilter lists={all.map((w) => w.tags)} value={tagF} onChange={setTagF} />
            <span className="lst-count" role="status">{filtered.length} résultat(s){folder ? ` dans « ${folder} »` : ""}</span>
            <span style={{ flex: 1 }} />
          {ai.enabled && can("edit") && (
            <button type="button" onClick={() => setAiOpen(true)} aria-haspopup="dialog">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" style={{ verticalAlign: "-3px", marginRight: 6 }}><path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z" /></svg>
              Générer avec l'IA
            </button>
          )}
          {can("edit") && <button type="button" onClick={() => setTemplatesOpen(true)} aria-haspopup="dialog">Partir d'un modèle</button>}
          {can("edit") && <button type="button" className="primary" aria-haspopup="dialog" onClick={() => { create.reset(); setName(""); setFolderNew(""); setNewTags([]); setCreating(true); }}>Nouveau workflow</button>}
          </div>
          {creating && can("edit") && (
            <Overlay variant="modal" title="Nouveau workflow" description="Donnez un nom : vous arriverez directement dans l'éditeur." onClose={() => setCreating(false)}
              footer={<><button type="button" onClick={() => setCreating(false)}>Annuler</button><button type="submit" form="wf-new" className="primary" disabled={!name.trim() || create.isPending}>Créer et ouvrir l'éditeur</button></>}>
              <form id="wf-new" onSubmit={(e: FormEvent) => { e.preventDefault(); if (name.trim()) create.mutate(); }}>
                <div className="field"><label htmlFor="wf-name">Nom du nouveau workflow</label>
                  <input id="wf-name" value={name} onChange={(e) => setName(e.target.value)} aria-describedby={err ? "wf-err" : undefined} aria-invalid={!!err} autoFocus /></div>
                <div className="field"><label htmlFor="wf-folder">Dossier (facultatif)</label><input id="wf-folder" value={folderNew} placeholder={folder || "ex. Réseau/DNS"} onChange={(e) => setFolderNew(e.target.value)} /></div>
                <TagInput value={newTags} onChange={setNewTags} suggestions={[...new Set(all.flatMap((w) => w.tags ?? []))].sort()} />
                {err && <p id="wf-err" role="alert" className="field-error">Erreur : {err.status === 400 ? "cet identifiant existe déjà, changez le nom." : err.message}</p>}
              </form>
            </Overlay>
          )}
          {aiOpen && ai.enabled && can("edit") && <AiDraftFlow mode={ai.mode} onClose={() => setAiOpen(false)} onOpenDraft={(slug) => navigate(`/workflows/${slug}/schema`)} />}
          {isError && <p role="alert">Une erreur est survenue. Réessayez.</p>}
          <div className="table-wrap">
            <table className="adm-table">
              <caption className="sr-only">Workflows</caption>
              <thead><tr><th scope="col"><span className="sr-only">Favori</span></th><SortTh label="Nom" field="name" sort={sort} onSort={toggle} /><SortTh label="Dossier" field="folder" sort={sort} onSort={toggle} /><th scope="col">Étiquettes</th><SortTh label="Version" field="status" sort={sort} onSort={toggle} /><SortTh label="Modifié le" field="updated" sort={sort} onSort={toggle} /><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {paged.slice.map((w) => (
                  <tr key={w.slug}>
                    <td><FavoriteStar kind="workflow" slug={w.slug} name={w.name} /></td>
                    <td><Link className="lst-title" to={`/workflows/${w.slug}`}>{w.name}</Link>{w.deprecated && <> <Badge tone="warn">⚠ déprécié</Badge></>}{w.description && <span className="lst-sub">{w.description}</span>}</td>
                    <td>{w.folder ? <span className="tag">{w.folder}</span> : "—"}</td>
                    <td>{(w.tags?.length ?? 0) > 0 ? <TagList tags={w.tags} onPick={setTagF} active={tagF} /> : "—"}</td>
                    <td>{w.latest_version ? <Badge tone="info">v{w.latest_version.version}</Badge> : <Badge tone="draft">brouillon</Badge>}</td>
                    <td>{formatDate(w.updated_at)}</td>
                    <td className="row-actions">
                      <Link className="icon-btn" to={`/workflows/${w.slug}/schema`} aria-label={`${can("edit") ? "Modifier" : "Consulter"} ${w.name}`} title={can("edit") ? "Modifier" : "Consulter"}><RowIcon name={can("edit") ? "edit" : "view"} /></Link>{" "}
                      {w.latest_version && can("run") && <Link className="icon-btn icon-btn-primary" to={`/workflows/${w.slug}/run`} aria-label={`Lancer ${w.name}`} title="Lancer"><RowIcon name="play" /></Link>}
                    </td>
                  </tr>
                ))}
                {filtered.length === 0 && <EmptyRow cols={7}>{isLoading ? "Chargement…" : <>Aucun workflow ici. {can("edit") ? "Créez-en un pour assembler des étapes, des scripts et des actions." : ""}</>}</EmptyRow>}
              </tbody>
            </table>
          </div>
          {paged.pager("Pagination des workflows")}
        </section>
      </div>
    </>
  );
}
