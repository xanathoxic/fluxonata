import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, apiAll, type AiSkill, type ApiError } from "../../api";
import { Badge, EmptyRow, SearchBox, SortTh, usePaged, useSort } from "../../components/AdminUi";
import { AiNotice, useAiMode } from "../../components/AiBox";
import { FolderTree, inFolder, useFolders } from "../../components/FolderTree";
import { MoveFolderOverlay } from "../../components/MoveFolderOverlay";
import { PageHeader } from "../../components/PageHeader";
import { RowIcon } from "../../components/RowIcon";
import { useApp } from "../../context";
import { SkillForm } from "./SkillForm";
import { TagFilter, TagList } from "../../components/Tags";

export function useSkills() {
  const { workspace } = useApp();
  return useQuery({ queryKey: ["ai-skills", workspace.slug], queryFn: () => apiAll<AiSkill>(`/workspaces/${workspace.slug}/ai/skills/`) });
}

/** Library > Skills IA: reusable AI capabilities (instructions, typed inputs, expected answer) selectable in a workflow's AI step. */
export function SkillsPage() {
  const { can, workspace } = useApp();
  const qc = useQueryClient();
  const ai = useAiMode();
  const q = useSkills();
  const [creating, setCreating] = useState(false);
  const [folder, setFolder] = useState("");
  const [search, setSearch] = useState("");
  const [tagF, setTagF] = useState("");
  const [moving, setMoving] = useState<AiSkill | null>(null);
  const move = useMutation<unknown, ApiError, { slug: string; folder: string }>({
    mutationFn: (i) => api(`/workspaces/${workspace.slug}/ai/skills/${i.slug}/`, { method: "PATCH", json: { folder: i.folder } }),
    onSuccess: () => { setMoving(null); void qc.invalidateQueries({ queryKey: ["ai-skills"] }); },
  });
  const all = q.data?.results ?? [];
  const folders = useMemo(() => all.map((s) => s.folder), [all]);
  const created = useFolders("ai_skill").data?.results ?? [];
  const shown = all.filter((s) => inFolder(s.folder, folder) && (!tagF || (s.tags ?? []).includes(tagF)) && (!search || `${s.name} ${s.description} ${(s.tags ?? []).join(" ")}`.toLowerCase().includes(search.toLowerCase())));
  const { sorted, sort, toggle } = useSort(shown, { name: (s) => s.name, folder: (s) => s.folder, version: (s) => s.latest_version?.version ?? "", status: (s) => s.status }, { key: "name", dir: "asc" });
  const paged = usePaged(sorted, 25);
  return (
    <>
      <PageHeader title="Skills IA" description="Compétences IA réutilisables : des instructions, des entrées typées et le format de la réponse. Elles se choisissent dans l'« Étape IA » d'un workflow."
        actions={can("publish") && <button type="button" className="primary" onClick={() => setCreating(true)}>Nouveau skill</button>} />
      {!ai.enabled && <p role="note" className="notice">L'IA est désactivée pour cet espace : vous pouvez préparer des skills, mais aucune étape IA ne s'exécutera tant qu'un administrateur n'a pas activé l'IA (Administration → Assistant IA).</p>}
      {ai.enabled && <AiNotice mode={ai.mode} />}
      <div className="wf-list-layout">
        <FolderTree label="Dossiers de skills" kind="ai_skill" canEdit={can("publish")} folders={folders} current={folder} onPick={setFolder} />
        <section className="adm-card lst-main">
          <div className="adm-filters">
            <SearchBox label="Rechercher un skill" value={search} onChange={setSearch} placeholder="Nom, description ou étiquette" />
            <TagFilter lists={all.map((s) => s.tags)} value={tagF} onChange={setTagF} />
            <span className="lst-count" role="status">{shown.length} skill(s)</span>
          </div>
          <div className="table-wrap">
            <table className="adm-table">
              <caption className="sr-only">Skills IA</caption>
              <thead><tr><SortTh label="Skill" field="name" sort={sort} onSort={toggle} /><SortTh label="Dossier" field="folder" sort={sort} onSort={toggle} /><th scope="col">Étiquettes</th><th scope="col">Entrées</th><SortTh label="Version" field="version" sort={sort} onSort={toggle} /><SortTh label="Statut" field="status" sort={sort} onSort={toggle} /><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {paged.slice.map((s) => (
                  <tr key={s.slug}>
                    <td><Link className="lst-title" to={`/library/skills/${s.slug}`}>{s.name}</Link>{s.description && <span className="lst-sub">{s.description}</span>}</td>
                    <td>{s.folder ? <span className="tag">{s.folder}</span> : "—"}</td>
                    <td>{(s.tags?.length ?? 0) > 0 ? <TagList tags={s.tags} onPick={setTagF} active={tagF} /> : "—"}</td>
                    <td>{Object.keys(s.latest_version?.input_schema.properties ?? {}).join(", ") || "—"}</td>
                    <td>{s.latest_version ? <Badge tone="info">v{s.latest_version.version}</Badge> : "—"}</td>
                    <td>{s.status === "deprecated" ? <Badge tone="warn">{`⚠ Déprécié${s.deprecation_reason ? ` : ${s.deprecation_reason}` : ""}`}</Badge> : <Badge tone="ok">✔ Actif</Badge>}</td>
                    <td className="row-actions">
                      <Link className="icon-btn" to={`/library/skills/${s.slug}`} aria-label={`Voir le skill ${s.name}`} title="Voir"><RowIcon name="eye" /></Link>
                      {can("publish") && <button type="button" className="icon-btn" onClick={() => { move.reset(); setMoving(s); }} aria-label={`Déplacer le skill ${s.name} dans un dossier`} title="Déplacer"><RowIcon name="folder" /></button>}
                    </td>
                  </tr>
                ))}
                {shown.length === 0 && <EmptyRow cols={7}>{q.isLoading ? "Chargement…" : <>Aucun skill ici. {can("publish") ? "Créez-en un pour le réutiliser dans plusieurs workflows." : ""}</>}</EmptyRow>}
              </tbody>
            </table>
          </div>
          {paged.pager("Pagination des skills")}
        </section>
      </div>
      {creating && <SkillForm folder={folder} onClose={() => setCreating(false)} />}
      {moving && <MoveFolderOverlay title={`Déplacer « ${moving.name} »`} current={moving.folder} folders={[...folders, ...created]} busy={move.isPending} error={move.error?.message} onSave={(f) => move.mutate({ slug: moving.slug, folder: f })} onClose={() => setMoving(null)} />}
    </>
  );
}
