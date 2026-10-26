import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, apiAll, type AiSkill, type ApiError } from "../../api";
import { AiNotice, useAiMode } from "../../components/AiBox";
import { FolderTree, inFolder, useFolders } from "../../components/FolderTree";
import { MoveFolderOverlay } from "../../components/MoveFolderOverlay";
import { PageHeader } from "../../components/PageHeader";
import { RowIcon } from "../../components/RowIcon";
import { useApp } from "../../context";
import { SkillForm } from "./SkillForm";

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
  const [moving, setMoving] = useState<AiSkill | null>(null);
  const move = useMutation<unknown, ApiError, { slug: string; folder: string }>({
    mutationFn: (i) => api(`/workspaces/${workspace.slug}/ai/skills/${i.slug}/`, { method: "PATCH", json: { folder: i.folder } }),
    onSuccess: () => { setMoving(null); void qc.invalidateQueries({ queryKey: ["ai-skills"] }); },
  });
  const all = q.data?.results ?? [];
  const folders = useMemo(() => all.map((s) => s.folder), [all]);
  const created = useFolders("ai_skill").data?.results ?? [];
  const shown = all.filter((s) => inFolder(s.folder, folder) && (!search || `${s.name} ${s.description}`.toLowerCase().includes(search.toLowerCase())));
  return (
    <>
      <PageHeader title="Skills IA" description="Compétences IA réutilisables : des instructions, des entrées typées et le format de la réponse. Elles se choisissent dans l'« Étape IA » d'un workflow."
        actions={can("publish") && <button type="button" className="primary" onClick={() => setCreating(true)}>Nouveau skill</button>} />
      {!ai.enabled && <p role="note" className="notice">L'IA est désactivée pour cet espace : vous pouvez préparer des skills, mais aucune étape IA ne s'exécutera tant qu'un administrateur n'a pas activé l'IA (Administration → Assistant IA).</p>}
      {ai.enabled && <AiNotice mode={ai.mode} />}
      <div className="wf-list-layout">
        <FolderTree label="Dossiers de skills" kind="ai_skill" canEdit={can("publish")} folders={folders} current={folder} onPick={setFolder} />
        <div>
          <div className="toolbar-row"><div className="field"><label htmlFor="sk-search">Rechercher un skill</label><input id="sk-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)} /></div></div>
          <div className="table-wrap">
            <table>
              <caption className="sr-only">Skills IA</caption>
              <thead><tr><th scope="col">Skill</th><th scope="col">Dossier</th><th scope="col">Entrées</th><th scope="col">Version</th><th scope="col">Statut</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {shown.map((s) => (
                  <tr key={s.slug}>
                    <td><Link to={`/library/skills/${s.slug}`}>{s.name}</Link><br /><span className="hint">{s.description}</span></td>
                    <td>{s.folder || "—"}</td>
                    <td>{Object.keys(s.latest_version?.input_schema.properties ?? {}).join(", ") || "—"}</td>
                    <td>{s.latest_version?.version ?? "—"}</td>
                    <td>{s.status === "deprecated" ? `⚠ Déprécié${s.deprecation_reason ? ` : ${s.deprecation_reason}` : ""}` : "✔ Actif"}</td>
                    <td className="row-actions">
                      <Link className="icon-btn" to={`/library/skills/${s.slug}`} aria-label={`Voir le skill ${s.name}`} title="Voir"><RowIcon name="eye" /></Link>
                      {can("publish") && <button type="button" className="icon-btn" onClick={() => { move.reset(); setMoving(s); }} aria-label={`Déplacer le skill ${s.name} dans un dossier`} title="Déplacer"><RowIcon name="folder" /></button>}
                    </td>
                  </tr>
                ))}
                {q.data && shown.length === 0 && <tr><td colSpan={6} className="hint">Aucun skill ici. {can("publish") ? "Créez-en un pour le réutiliser dans plusieurs workflows." : ""}</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>
      {creating && <SkillForm folder={folder} onClose={() => setCreating(false)} />}
      {moving && <MoveFolderOverlay title={`Déplacer « ${moving.name} »`} current={moving.folder} folders={[...folders, ...created]} busy={move.isPending} error={move.error?.message} onSave={(f) => move.mutate({ slug: moving.slug, folder: f })} onClose={() => setMoving(null)} />}
    </>
  );
}
