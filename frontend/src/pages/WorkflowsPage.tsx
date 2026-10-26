import { FolderTree, inFolder } from "../components/FolderTree";
import { RowIcon } from "../components/RowIcon";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, apiAll, type ApiError, type WorkflowSummary } from "../api";
import { AiNotice, useAiMode } from "../components/AiBox";
import { FavoriteStar } from "../components/FavoriteStar";
import { Overlay } from "../components/Overlay";
import { useApp } from "../context";
import { formatDate } from "../i18n";

const slugify = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 50);

interface DraftIssue { node?: string; level: "error" | "warning"; code: string; message: string }
interface DraftResult { workflow: string; issues: DraftIssue[]; graph: { nodes: { id: string; name?: string }[] } }

export function WorkflowsPage() {
  const { workspace, can } = useApp();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [folderNew, setFolderNew] = useState("");
  const [creating, setCreating] = useState(false);
  const [folder, setFolder] = useState("");
  const [q, setQ] = useState("");
  const ai = useAiMode();
  const [aiText, setAiText] = useState("");
  const [aiName, setAiName] = useState("");
  const [aiOpen, setAiOpen] = useState(false);
  const { data, isLoading, isError } = useQuery({ queryKey: ["workflows", workspace.slug], queryFn: () => apiAll<WorkflowSummary>(`/workspaces/${workspace.slug}/workflows/`) });
  const create = useMutation({
    mutationFn: () => api<WorkflowSummary>(`/workspaces/${workspace.slug}/workflows/`, { json: { name, slug: slugify(name) || "workflow", folder: folderNew || folder } }),
    onSuccess: (w) => { void qc.invalidateQueries({ queryKey: ["workflows"] }); navigate(`/workflows/${w.slug}/schema`); },
  });
  const draft = useMutation<DraftResult, ApiError>({
    mutationFn: () => api(`/workspaces/${workspace.slug}/ai/draft-workflow/`, { json: { request: aiText, name: aiName || "Brouillon IA", slug: slugify(aiName || `brouillon-ia-${Date.now() % 100000}`) } }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["workflows"] }); },
  });
  const all = data?.results ?? [];
  const folders = useMemo(() => all.map((w) => w.folder), [all]);
  const shown = all.filter((w) => inFolder(w.folder, folder) && (!q || `${w.name} ${w.description}`.toLowerCase().includes(q.toLowerCase())));
  const err = create.error as ApiError | null;
  return (
    <>
      <h1>Workflows</h1>
      <div className="wf-list-layout">
        <FolderTree label="Dossiers de workflows" kind="workflow" canEdit={can("edit")} folders={folders} current={folder} onPick={setFolder} />
        <div>
          <div className="inline-form">
            <div className="field"><label htmlFor="wf-search">Rechercher un workflow</label><input id="wf-search" type="search" value={q} onChange={(e) => setQ(e.target.value)} /></div>
            {ai.enabled && can("edit") && (
              <button type="button" onClick={() => setAiOpen(true)} aria-haspopup="dialog">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" style={{ verticalAlign: "-3px", marginRight: 6 }}><path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z" /></svg>
                Générer avec l'IA
              </button>
            )}
            {can("edit") && <button type="button" className="primary" aria-expanded={creating} onClick={() => setCreating(!creating)}>{creating ? "Fermer" : "Nouveau workflow"}</button>}
          </div>
          {creating && can("edit") && (
            <form onSubmit={(e: FormEvent) => { e.preventDefault(); if (name.trim()) create.mutate(); }} className="card inline-form">
              <div className="field"><label htmlFor="wf-name">Nom du nouveau workflow</label>
                <input id="wf-name" value={name} onChange={(e) => setName(e.target.value)} aria-describedby={err ? "wf-err" : undefined} aria-invalid={!!err} /></div>
              <div className="field"><label htmlFor="wf-folder">Dossier (facultatif)</label><input id="wf-folder" value={folderNew} placeholder={folder || "ex. Réseau/DNS"} onChange={(e) => setFolderNew(e.target.value)} /></div>
              <button type="submit" className="primary" disabled={!name.trim() || create.isPending}>Créer et ouvrir l'éditeur</button>
              {err && <p id="wf-err" role="alert" className="field-error">Erreur : {err.status === 400 ? "cet identifiant existe déjà, changez le nom." : err.message}</p>}
            </form>
          )}
          {aiOpen && ai.enabled && can("edit") && (draft.data ? (
            <Overlay wide title="Brouillon généré" onClose={() => { setAiOpen(false); draft.reset(); }}
              description="Le brouillon est enregistré, pas publié. Relisez-le dans l'éditeur avant toute publication."
              footer={<button type="button" className="primary" onClick={() => navigate(`/workflows/${draft.data.workflow}/schema`)}>Ouvrir dans l'éditeur</button>}>
              <DraftReport result={draft.data} />
            </Overlay>
          ) : (
            <Overlay wide title="Générer un brouillon avec l'assistant IA" onClose={() => setAiOpen(false)}
              description="Décrivez le workflow souhaité : l'assistant propose un brouillon que vous relisez, corrigez et publiez vous-même."
              footer={<><button type="button" onClick={() => setAiOpen(false)}>Annuler</button>
                <button type="submit" form="ai-draft-form" className="primary" disabled={!aiText.trim() || draft.isPending}>{draft.isPending ? "Génération en cours…" : "Générer un brouillon"}</button></>}>
              <form id="ai-draft-form" onSubmit={(e: FormEvent) => { e.preventDefault(); if (aiText.trim()) draft.mutate(); }}>
                <div className="field"><label htmlFor="ai-req">Décrivez en français ce que le workflow doit faire</label><textarea id="ai-req" rows={5} value={aiText} onChange={(e) => setAiText(e.target.value)} /></div>
                <div className="field"><label htmlFor="wf-name-ai">Nom du brouillon</label><input id="wf-name-ai" placeholder="ex. Redémarrer des VM" value={aiName} onChange={(e) => setAiName(e.target.value)} /></div>
                <AiNotice mode={ai.mode} />
                <p className="hint">Le résultat est toujours un brouillon : vous le relisez, le corrigez et le publiez vous-même.</p>
                {draft.isPending && <p role="status" className="hint">L'assistant rédige le brouillon, cela peut prendre une minute avec un modèle local…</p>}
                {draft.error && <p role="alert" className="field-error">Erreur : {draft.error.message}</p>}
              </form>
            </Overlay>
          ))}
          {isLoading && <p role="status">Chargement…</p>}
          {isError && <p role="alert">Une erreur est survenue. Réessayez.</p>}
          {data && shown.length === 0 && <p>Aucun workflow ici. {can("edit") ? "Créez-en un pour assembler des étapes, des scripts et des actions." : ""}</p>}
          {shown.length > 0 && (
            <div className="table-wrap">
              <table>
                <caption className="sr-only">Workflows</caption>
                <thead><tr><th scope="col"><span className="sr-only">Favori</span></th><th scope="col">Nom</th><th scope="col">Dossier</th><th scope="col">Version</th><th scope="col">Modifié le</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
                <tbody>
                  {shown.map((w) => (
                    <tr key={w.slug}>
                      <td><FavoriteStar kind="workflow" slug={w.slug} name={w.name} /></td>
                      <td><Link to={`/workflows/${w.slug}`}>{w.name}</Link>{w.deprecated && " ⚠ déprécié"}</td>
                      <td>{w.folder || "—"}</td><td>{w.latest_version?.version ?? "brouillon"}</td><td>{formatDate(w.updated_at)}</td>
                      <td className="row-actions">
                        <Link className="icon-btn" to={`/workflows/${w.slug}/schema`} aria-label={`${can("edit") ? "Modifier" : "Consulter"} ${w.name}`} title={can("edit") ? "Modifier" : "Consulter"}><RowIcon name={can("edit") ? "edit" : "view"} /></Link>{" "}
                        {w.latest_version && can("run") && <Link className="icon-btn icon-btn-primary" to={`/workflows/${w.slug}/run`} aria-label={`Lancer ${w.name}`} title="Lancer"><RowIcon name="play" /></Link>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function DraftReport({ result }: { result: DraftResult }) {
  const names = new Map(result.graph.nodes.map((n) => [n.id, n.name || n.id]));
  const errors = result.issues.filter((i) => i.level === "error");
  const warnings = result.issues.filter((i) => i.level !== "error");
  return (
    <div role="status">
      <p><strong>{result.graph.nodes.length} étape(s) proposée(s).</strong>{" "}
        {errors.length === 0 && warnings.length === 0 && "Aucun problème détecté par la validation."}
        {errors.length > 0 && <>{errors.length} problème(s) bloquant(s) à corriger avant de pouvoir publier.</>}
        {warnings.length > 0 && <> {warnings.length} avertissement(s).</>}
      </p>
      {result.issues.length > 0 && (
        <ul>
          {result.issues.map((i, k) => (
            <li key={k}><span className={i.level === "error" ? "field-error" : "hint"}>{i.level === "error" ? "✖ Bloquant" : "⚠ Avertissement"}</span>
              {i.node && <> · <strong>{names.get(i.node) ?? i.node}</strong></>} : {i.message}</li>
          ))}
        </ul>
      )}
      <p className="hint">Ouvrez l'éditeur : chaque étape en erreur est repérée sur le schéma, et « Valider » refait ce contrôle.</p>
    </div>
  );
}
