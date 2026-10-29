import { useState } from "react";
import { formatDate } from "../../i18n";
import { errorText, useWsMutation, useWsQuery } from "../../lib";
import { HelpTip } from "../../components/HelpTip";

interface Repo { provider: string; repo: string; branch: string; path: string; connection: string }
interface Job { id: number; kind: string; status: string; status_label: string; requested_by: string | null; created_at: string; finished_at: string | null; params: { overwrite?: boolean }; result: JobResult | null; error: { code: string; message: string; next_step: string } | null }
interface JobResult { status?: string; url?: string; branch?: string; created?: string[]; updated?: string[]; unchanged?: number; only_in_repo?: string[]; applied?: boolean; head?: string; results?: { slug: string; status: string; reason?: string }[] }
interface Conn { name: string; label: string; kind: string }

const KINDS: Record<string, string> = { check: "Test d'accès", push: "Envoi (demande de fusion)", pull_preview: "Aperçu de l'import", pull_apply: "Import en brouillons" };
const VERB: Record<string, string> = { created: "créé", updated: "mis à jour", would_create: "serait créé", would_update: "serait mis à jour", skipped: "ignoré", refused: "refusé" };
const safeUrl = (u?: string) => (u && /^https:\/\//.test(u) ? u : undefined);

function Summary({ job }: { job: Job }) {
  const r = job.result;
  if (job.status === "failed" && job.error) return <span className="field-error">{job.error.message} {job.error.next_step}</span>;
  if (!r) return <span className="hint">{job.status_label}</span>;
  if (job.kind === "check") return <span>Accès valide (tête de branche {r.head?.slice(0, 7)}).</span>;
  if (job.kind === "push") {
    if (r.status === "unchanged") return <span>Rien à envoyer : le dépôt est à jour ({r.unchanged} fichier(s) identique(s)).</span>;
    const link = safeUrl(r.url);
    return <span>{r.created?.length ?? 0} créé(s), {r.updated?.length ?? 0} modifié(s) sur la branche <code>{r.branch}</code>. {link ? <a href={link} target="_blank" rel="noopener noreferrer">Ouvrir la demande de fusion</a> : null}{(r.only_in_repo?.length ?? 0) > 0 && <span className="hint"> Présents seulement dans le dépôt (jamais supprimés) : {r.only_in_repo?.join(", ")}.</span>}</span>;
  }
  return <span>{(r.results ?? []).map((x) => `${x.slug} : ${VERB[x.status] ?? x.status}${x.reason ? ` (${x.reason})` : ""}`).join(" · ") || "Aucun fichier."}</span>;
}

/** Synchronisation of the workspace's workflows with a GitHub / GitLab repository. Changes leave as a pull / merge request
 *  on a new branch (never on the main branch) and come back as DRAFTS; a worker does the calls, this page follows them. */
export function GitTab() {
  const cfg = useWsQuery<{ repo: Repo | null }>("git-config", "/git/");
  const conns = useWsQuery<{ results: Conn[] }>("connections", "/connections/");
  const jobs = useWsQuery<{ results: Job[] }>("git-jobs", "/git/jobs/", { refetchInterval: 2500 });
  const save = useWsMutation<Repo>("PUT", "/git/", ["git-config"]);
  const remove = useWsMutation<unknown>("DELETE", "/git/", ["git-config"]);
  const start = useWsMutation<{ kind: string; overwrite?: boolean }>("POST", "/git/jobs/", ["git-jobs"]);
  const [draft, setDraft] = useState<Repo | null>(null);
  const [overwrite, setOverwrite] = useState(false);
  const repo = cfg.data?.repo ?? null;
  const form: Repo = draft ?? repo ?? { provider: "github", repo: "", branch: "main", path: "workflows", connection: "" };
  const set = (patch: Partial<Repo>) => setDraft({ ...form, ...patch });
  const tokens = (conns.data?.results ?? []).filter((c) => c.kind === "http_bearer");
  const busy = (jobs.data?.results ?? []).some((j) => j.status === "queued" || j.status === "running");
  const err = save.error ?? remove.error ?? start.error;
  return (
    <section aria-labelledby="git-h" className="adm-card">
      <h2 id="git-h">Git : GitHub ou GitLab <HelpTip>Les workflows partent sur une <strong>nouvelle branche</strong> avec une demande de fusion à relire : jamais sur la branche principale. L'import lit la branche et met à jour des <strong>brouillons</strong> (rien n'est publié ni lancé). Les droits de la personne s'appliquent : les dossiers privés qu'elle ne voit pas ne sont pas envoyés. Le jeton d'accès vient d'une connexion « Service HTTP » dont l'adresse est celle de l'API (GitHub : <code>https://api.github.com</code>, GitLab : <code>https://gitlab.com/api/v4</code> ou votre instance).</HelpTip></h2>
      <form className="adm-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(form, { onSuccess: () => setDraft(null) }); }}>
        <div className="field"><label htmlFor="git-prov">Fournisseur</label>
          <select id="git-prov" value={form.provider} onChange={(e) => set({ provider: e.target.value })}><option value="github">GitHub</option><option value="gitlab">GitLab</option></select></div>
        <div className="field"><label htmlFor="git-repo">Dépôt</label>
          <input id="git-repo" value={form.repo} placeholder={form.provider === "github" ? "propriétaire/nom" : "groupe/projet"} onChange={(e) => set({ repo: e.target.value })} required /></div>
        <div className="field"><label htmlFor="git-branch">Branche principale</label><input id="git-branch" value={form.branch} onChange={(e) => set({ branch: e.target.value })} required /></div>
        <div className="field">
<HelpTip>Un fichier <code>&lt;identifiant&gt;.json</code> par workflow.</HelpTip><label htmlFor="git-path">Dossier du dépôt</label><input id="git-path" value={form.path} onChange={(e) => set({ path: e.target.value })} required /></div>
        <div className="field"><label htmlFor="git-conn">Connexion (jeton d'accès)</label>
          <select id="git-conn" value={form.connection} onChange={(e) => set({ connection: e.target.value })} required><option value="">Choisir…</option>{tokens.map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}</select></div>
        <div className="toolbar-row">
          <button type="submit" className="primary" disabled={save.isPending}>Enregistrer la configuration</button>
          {repo && <button type="button" onClick={() => remove.mutate(undefined, { onSuccess: () => setDraft(null) })}>Retirer la configuration</button>}
        </div>
      </form>
      {err && <p role="alert" className="field-error">Erreur : {errorText(err)}</p>}
      {repo && (
        <>
          <h3>Opérations</h3>
          <div className="toolbar-row">
            <button type="button" disabled={busy || start.isPending} onClick={() => start.mutate({ kind: "check" })}>Tester l'accès</button>
            <button type="button" className="primary" disabled={busy || start.isPending} onClick={() => start.mutate({ kind: "push" })}>Proposer les modifications (demande de fusion)</button>
            <button type="button" disabled={busy || start.isPending} onClick={() => start.mutate({ kind: "pull_preview", overwrite })}>Aperçu de l'import</button>
            <button type="button" disabled={busy || start.isPending} onClick={() => start.mutate({ kind: "pull_apply", overwrite })}>Importer en brouillons</button>
          </div>
          <div className="field field-inline"><input id="git-ow" type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} /><label htmlFor="git-ow">Remplacer les brouillons existants (les éditeurs ouverts verront un conflit)</label></div>
          {busy && <p role="status" className="hint">Une opération est en cours : un travailleur l'exécute…</p>}
          <div className="table-wrap"><table>
            <caption className="sr-only">Dernières opérations Git</caption>
            <thead><tr><th scope="col">Date</th><th scope="col">Opération</th><th scope="col">Par</th><th scope="col">État</th><th scope="col">Résultat</th></tr></thead>
            <tbody>
              {jobs.data?.results.map((j) => (
                <tr key={j.id}><td>{formatDate(j.created_at)}</td><td>{KINDS[j.kind] ?? j.kind}</td><td>{j.requested_by ?? "—"}</td><td>{j.status_label}</td><td><Summary job={j} /></td></tr>
              ))}
              {jobs.data?.results.length === 0 && <tr><td colSpan={5} className="hint">Aucune opération pour l'instant.</td></tr>}
            </tbody>
          </table></div>
        </>
      )}
    </section>
  );
}
