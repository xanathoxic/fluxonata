import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type AiSkill, type ApiError, type JsonSchema } from "../../api";
import { useFolders } from "../../components/FolderTree";
import { MoveFolderOverlay } from "../../components/MoveFolderOverlay";
import { PageHeader } from "../../components/PageHeader";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { useWsQuery } from "../../lib";
import { NotFoundPage } from "../NotFoundPage";
import { useSkills } from "./SkillsPage";
import { SkillForm } from "./SkillForm";
import { schemaToFields } from "./fields";
import { HelpTip } from "../../components/HelpTip";
import { TagInput, TagList } from "../../components/Tags";

const TYPE: Record<string, string> = { string: "Texte", integer: "Entier", number: "Nombre", boolean: "Oui / non" };

function FieldsTable({ schema, label }: { schema: JsonSchema; label: string }) {
  const rows = schemaToFields(schema);
  return (
    <div className="table-wrap"><table><caption className="sr-only">{label}</caption>
      <thead><tr><th scope="col">Champ</th><th scope="col">Type</th><th scope="col">Obligatoire</th><th scope="col">Description</th></tr></thead>
      <tbody>{rows.map((f) => <tr key={f.name}><td className="mono">{f.name}</td><td>{TYPE[f.type] ?? f.type}</td><td>{f.required ? "Oui" : "Non"}</td><td>{f.description || "—"}</td></tr>)}
        {rows.length === 0 && <tr><td colSpan={4} className="hint">Aucun champ.</td></tr>}</tbody></table></div>
  );
}

export function SkillDetailPage() {
  const { slug = "" } = useParams();
  const { workspace, can } = useApp();
  const qc = useQueryClient();
  const detail = useQuery({ queryKey: ["ai-skill", workspace.slug, slug], queryFn: () => api<AiSkill>(`/workspaces/${workspace.slug}/ai/skills/${slug}/`) });
  const usage = useWsQuery<{ results: { workflow: string; workflow_version: string; skill_versions: string[] }[] }>(`ai-skill-dep-${slug}`, `/ai/skills/${slug}/dependents/`);
  const all = useSkills();
  const created = useFolders("ai_skill").data?.results ?? [];
  const [newVersion, setNewVersion] = useState(false);
  const [draftTags, setDraftTags] = useState<string[] | null>(null);
  const saveTags = useMutation<unknown, ApiError, string[]>({ mutationFn: (tags) => api(`/workspaces/${workspace.slug}/ai/skills/${slug}/`, { method: "PATCH", json: { tags } }), onSuccess: () => { setDraftTags(null); void qc.invalidateQueries({ queryKey: ["ai-skill"] }); void qc.invalidateQueries({ queryKey: ["ai-skills"] }); } });
  const [moving, setMoving] = useState(false);
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["ai-skill"] }); void qc.invalidateQueries({ queryKey: ["ai-skills"] }); };
  const move = useMutation<unknown, ApiError, string>({ mutationFn: (folder) => api(`/workspaces/${workspace.slug}/ai/skills/${slug}/`, { method: "PATCH", json: { folder } }), onSuccess: () => { setMoving(false); refresh(); } });
  const deprecate = useMutation<unknown, ApiError, boolean>({ mutationFn: (reinstate) => api(`/workspaces/${workspace.slug}/ai/skills/${slug}/deprecate/`, { json: reinstate ? { reinstate: true } : { reason: "Remplacé : voir un autre skill" } }), onSuccess: refresh });
  if (detail.isError) return <NotFoundPage title="Skill introuvable" message="Ce skill n'existe pas ou vous n'avez pas le droit de le voir." />;
  const s = detail.data;
  if (!s) return <p role="status">Chargement…</p>;
  const v = s.latest_version;
  return (
    <>
      <nav aria-label="Fil d'Ariane" className="breadcrumb"><Link to="/library/skills">Skills IA</Link> / <span aria-current="page">{s.name}</span></nav>
      <PageHeader title={s.name} description={s.description || undefined}
        actions={can("publish") && <>
          <button type="button" className="primary" onClick={() => setNewVersion(true)}>Nouvelle version</button>
          <button type="button" onClick={() => setMoving(true)}>Déplacer</button>
          <button type="button" onClick={() => deprecate.mutate(s.status === "deprecated")}>{s.status === "deprecated" ? "Réactiver" : "Déprécier"}</button></>} />
      <p className="meta"><span>{s.status === "deprecated" ? "⚠ Déprécié" : "✔ Actif"}</span><span>Dossier : {s.folder || "—"}</span><span>Propriétaire : {s.owner ?? "—"}</span>{v && <span>Version {v.version}</span>}</p>
      {(can("publish") || (s.tags?.length ?? 0) > 0) && (
        <section className="adm-card tag-card" aria-label="Étiquettes du skill">
          {can("publish")
            ? <TagInput value={draftTags ?? s.tags ?? []} onChange={setDraftTags} suggestions={[...new Set((all.data?.results ?? []).flatMap((x) => x.tags ?? []))].sort()} />
            : <TagList tags={s.tags} />}
          {draftTags && <div className="adm-savebar"><button type="button" className="primary" disabled={saveTags.isPending} onClick={() => saveTags.mutate(draftTags)}>Enregistrer les étiquettes</button><button type="button" onClick={() => setDraftTags(null)}>Annuler</button>{saveTags.error && <span role="alert" className="field-error">Erreur : {saveTags.error.message}</span>}</div>}
        </section>
      )}
      {v && (
        <div className="detail-grid">
          <section className="card" aria-labelledby="sk-ins">
            <h2 id="sk-ins">Instructions (version {v.version})</h2>
            <pre tabIndex={0} aria-label="Instructions du skill" className="wrap">{v.instructions}</pre>
            <h3>Entrées</h3><FieldsTable schema={v.input_schema} label="Entrées du skill" />
            <h3>Réponse attendue</h3><FieldsTable schema={v.output_schema} label="Champs de la réponse" />
          </section>
          <aside className="card" aria-label="Informations sur le skill">
            <dl className="facts">
              <dt>Fournisseur par défaut</dt><dd>{v.default_provider || "—"}</dd>
              <dt>Empreinte</dt><dd className="mono">{v.digest.slice(7, 19)}…</dd>
              <dt>Publiée le</dt><dd>{formatDate(v.created_at)}</dd>
            </dl>
            <h2>Utilisé par</h2>
            {usage.data?.results.length ? <ul className="plain">{usage.data.results.map((u) => <li key={`${u.workflow}${u.workflow_version}`}><Link to={`/workflows/${u.workflow}`}>{u.workflow}</Link> v{u.workflow_version} <span className="hint">(skill {u.skill_versions.join(", ")})</span></li>)}</ul> : <p className="hint">Aucun workflow publié n'utilise ce skill.</p>}
          </aside>
        </div>
      )}
      <section className="card" aria-labelledby="sk-hist" style={{ marginTop: 16 }}>
        <h2 id="sk-hist">Historique des versions <HelpTip>Une version publiée est immuable : un workflow publié garde la version du skill avec laquelle il a été publié.</HelpTip></h2>
        <div className="table-wrap"><table><caption className="sr-only">Versions du skill</caption>
          <thead><tr><th scope="col">Version</th><th scope="col">Publiée le</th><th scope="col">Note</th><th scope="col">Empreinte</th></tr></thead>
          <tbody>{s.versions?.map((x, i) => <tr key={x.version}><td>{x.version}{i === 0 ? " (dernière)" : ""}</td><td>{formatDate(x.created_at)}</td><td>{x.changelog || "—"}</td><td className="mono">{x.digest.slice(7, 19)}…</td></tr>)}</tbody></table></div>
      </section>
      {newVersion && <SkillForm skill={s} onClose={() => setNewVersion(false)} />}
      {moving && <MoveFolderOverlay title={`Déplacer « ${s.name} »`} current={s.folder} folders={[...(all.data?.results ?? []).map((x) => x.folder), ...created]} busy={move.isPending} error={move.error?.message} onSave={(f) => move.mutate(f)} onClose={() => setMoving(false)} />}
    </>
  );
}
