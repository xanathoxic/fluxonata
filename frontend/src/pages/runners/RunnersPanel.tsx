import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { api, type ApiError } from "../../api";
import { Overlay } from "../../components/Overlay";
import { RowIcon } from "../../components/RowIcon";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { errorText } from "../../lib";

export interface RunnerRow {
  id: string; name: string; description: string; scope: "shared" | "dedicated"; workspaces: string[]; tags: string[]; runtimes: string[];
  max_concurrent: number; paused: boolean; status: "online" | "offline" | "paused" | "never"; last_contact_at: string | null; last_ip: string | null;
  agent_version: string; platform: string; token_prefix: string; created_by: string | null; running_jobs: number; managed?: boolean;
}
type Created = RunnerRow & { token: string; token_notice: string };
const RUNTIMES: [string, string][] = [["python", "Python"], ["node", "Node.js"], ["typescript", "TypeScript"], ["shell", "Shell"], ["powershell", "PowerShell"]];
const STATUS: Record<RunnerRow["status"], [string, string]> = {
  online: ["● En ligne", "runner-online"], offline: ["○ Hors ligne", "runner-offline"], paused: ["⏸ En pause", "runner-paused"], never: ["◌ Jamais connecté", "runner-never"],
};

/** How to start the agent, ready to paste. The address is the one this page is served from: replace it if the runner
 *  reaches the platform another way. The token appears here once and never again. */
function startCommands(url: string, token: string) {
  return {
    host: `ORCH_URL=${url} ORCH_RUNNER_TOKEN=${token} python3 runner/agent.py`,
    docker: `sudo mkdir -p /var/lib/fluxonata-runner && sudo chown 10001 /var/lib/fluxonata-runner\ndocker build -f runner/Dockerfile -t fluxonata-runner .\ndocker run -d --name fluxonata-runner --restart unless-stopped \\\n  --group-add "$(stat -c %g /var/run/docker.sock)" \\\n  -e ORCH_URL=${url} -e ORCH_RUNNER_TOKEN=${token} \\\n  -e ORCH_WORK_DIR=/var/lib/fluxonata-runner -v /var/lib/fluxonata-runner:/var/lib/fluxonata-runner \\\n  -v /var/run/docker.sock:/var/run/docker.sock fluxonata-runner`,
  };
}

function TokenView({ created, onClose }: { created: Created; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const cmd = startCommands(window.location.origin, created.token);
  const copy = async () => { try { await navigator.clipboard.writeText(created.token); setCopied(true); } catch { setCopied(false); } };
  return (
    <Overlay wide title={`Runner « ${created.name} » créé`} description="Le jeton ne sera plus jamais affiché : copiez-le maintenant." onClose={onClose}
      footer={<button type="button" className="primary" onClick={onClose}>J'ai copié le jeton</button>}>
      <div role="alert" className="onetime"><strong>{created.token_notice}</strong><br /><code>{created.token}</code></div>
      <p><button type="button" onClick={() => void copy()}>Copier le jeton</button> {copied && <span role="status" className="hint">Copié.</span>}</p>
      <h3>Démarrer l'agent</h3>
      <p className="hint">Sur la machine qui exécutera les actions (Docker requis). L'adresse ci-dessous est celle de cette page : adaptez-la si le runner joint la plate-forme autrement (utilisez HTTPS hors réseau local).</p>
      <h4>Directement sur l'hôte</h4>
      <pre tabIndex={0} aria-label="Commande pour démarrer l'agent sur l'hôte" className="wrap">{cmd.host}</pre>
      <h4>Dans un conteneur</h4>
      <pre tabIndex={0} aria-label="Commandes pour démarrer l'agent dans un conteneur" className="wrap">{cmd.docker}</pre>
      <p className="hint">Le runner ne fait que des appels sortants vers la plate-forme ; il n'accède ni à la base de données ni au coffre-fort. Voir <code>docs/RUNNERS.md</code>.</p>
    </Overlay>
  );
}

function RunnerForm({ mode, runner, spaces, onClose, onCreated }: {
  mode: "platform" | "workspace"; runner?: RunnerRow; spaces: { slug: string; name: string }[]; onClose: () => void; onCreated: (c: Created) => void;
}) {
  const { workspace } = useApp();
  const qc = useQueryClient();
  const id = useId();
  const [name, setName] = useState(runner?.name ?? "");
  const [description, setDescription] = useState(runner?.description ?? "");
  const [scope, setScope] = useState<"shared" | "dedicated">(runner?.scope ?? (mode === "platform" ? "dedicated" : "dedicated"));
  const [picked, setPicked] = useState<string[]>(runner?.workspaces ?? []);
  const [tags, setTags] = useState((runner?.tags ?? []).join(", "));
  const [runtimes, setRuntimes] = useState<string[]>(runner?.runtimes ?? RUNTIMES.map(([k]) => k));
  const [max, setMax] = useState(runner?.max_concurrent ?? 2);
  const base = mode === "platform" ? "/platform/runners/" : `/workspaces/${workspace.slug}/runners/`;
  const body = { name, description, scope: mode === "platform" ? scope : "dedicated", workspaces: mode === "platform" && scope === "dedicated" ? picked : undefined,
    tags: tags.split(",").map((t) => t.trim()).filter(Boolean), runtimes, max_concurrent: max };
  const save = useMutation<Created | RunnerRow, ApiError>({
    mutationFn: () => runner ? api(`${base}${runner.id}/`, { method: "PATCH", json: body }) : api(base, { json: body }),
    onSuccess: (r) => { void qc.invalidateQueries({ queryKey: ["runners"] }); if (runner) onClose(); else onCreated(r as Created); },
  });
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  return (
    <Overlay wide title={runner ? `Modifier « ${runner.name} »` : "Nouveau runner"} onClose={onClose}
      description={mode === "workspace" ? `Dédié à l'espace « ${workspace.name} » : il n'exécute que les actions de cet espace.` : "Un agent installé sur une machine qui exécute les actions, comme un runner GitLab."}
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="submit" form={`${id}-f`} className="primary" disabled={save.isPending || runtimes.length === 0}>{runner ? "Enregistrer" : "Créer le runner"}</button></>}>
      <form id={`${id}-f`} onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <div className="field"><label htmlFor={`${id}-n`}>Nom</label><input id={`${id}-n`} value={name} onChange={(e) => setName(e.target.value)} required pattern="[a-z0-9][a-z0-9_-]{1,58}[a-z0-9]" className="mono" /><p className="hint">Minuscules, chiffres, - et _ (3 à 60 caractères).</p></div>
        <div className="field"><label htmlFor={`${id}-d`}>Description</label><input id={`${id}-d`} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={200} /></div>
        {mode === "platform" && (
          <fieldset>
            <legend>Portée</legend>
            <div className="field field-inline"><input type="radio" id={`${id}-sc1`} name={`${id}-scope`} checked={scope === "dedicated"} onChange={() => setScope("dedicated")} /><label htmlFor={`${id}-sc1`}>Dédié à des espaces</label></div>
            <div className="field field-inline"><input type="radio" id={`${id}-sc2`} name={`${id}-scope`} checked={scope === "shared"} onChange={() => setScope("shared")} /><label htmlFor={`${id}-sc2`}>Partagé (tous les espaces qui acceptent les runners partagés)</label></div>
            {scope === "dedicated" && (
              <fieldset className="bare"><legend>Espaces servis</legend>
                {spaces.map((s) => <div className="field field-inline" key={s.slug}><input type="checkbox" id={`${id}-w-${s.slug}`} checked={picked.includes(s.slug)} onChange={() => setPicked(toggle(picked, s.slug))} /><label htmlFor={`${id}-w-${s.slug}`}>{s.name} <span className="hint mono">{s.slug}</span></label></div>)}
              </fieldset>
            )}
          </fieldset>
        )}
        <div className="field"><label htmlFor={`${id}-t`}>Étiquettes</label><input id={`${id}-t`} value={tags} onChange={(e) => setTags(e.target.value)} placeholder="ex. linux, gpu" /><p className="hint">Séparées par des virgules. Une action qui exige des étiquettes ne s'exécute que sur un runner qui les porte toutes.</p></div>
        <fieldset><legend>Langages que ce runner peut exécuter</legend>
          {RUNTIMES.map(([k, label]) => <div className="field field-inline" key={k}><input type="checkbox" id={`${id}-r-${k}`} checked={runtimes.includes(k)} onChange={() => setRuntimes(toggle(runtimes, k))} /><label htmlFor={`${id}-r-${k}`}>{label}</label></div>)}
          <p className="hint">Les étapes IA ne s'exécutent jamais sur un runner : elles restent sur la plate-forme.</p>
        </fieldset>
        <div className="field"><label htmlFor={`${id}-m`}>Tâches en parallèle (au plus)</label><input id={`${id}-m`} type="number" min={1} max={32} value={max} onChange={(e) => setMax(Number(e.target.value))} /></div>
        {save.error && <p role="alert" className="field-error">Erreur : {errorText(save.error)}</p>}
      </form>
    </Overlay>
  );
}

/** Runners of the platform (platform administrators) or dedicated to the current space (space administrators). */
export function RunnersPanel({ mode }: { mode: "platform" | "workspace" }) {
  const { workspace } = useApp();
  const qc = useQueryClient();
  const base = mode === "platform" ? "/platform/runners/" : `/workspaces/${workspace.slug}/runners/`;
  const list = useQuery({ queryKey: ["runners", mode, workspace.slug], queryFn: () => api<{ results: RunnerRow[]; use_shared_runners?: boolean; shared_online?: number }>(base), refetchInterval: 5000 });
  const spaces = useQuery({ queryKey: ["platform", "spaces"], queryFn: () => api<{ results: { slug: string; name: string }[] }>("/platform/workspaces/"), enabled: mode === "platform" });
  const [editing, setEditing] = useState<RunnerRow | "new" | null>(null);
  const [created, setCreated] = useState<Created | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<RunnerRow | null>(null);
  const [confirmRotate, setConfirmRotate] = useState<RunnerRow | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["runners"] });
  const pause = useMutation<unknown, ApiError, RunnerRow>({ mutationFn: (r) => api(`${base}${r.id}/`, { method: "PATCH", json: { paused: !r.paused } }), onSuccess: refresh });
  const remove = useMutation<unknown, ApiError, RunnerRow>({ mutationFn: (r) => api(`${base}${r.id}/`, { method: "DELETE" }), onSuccess: () => { setConfirmDelete(null); refresh(); } });
  const rotate = useMutation<Created, ApiError, RunnerRow>({ mutationFn: (r) => api(`${base}${r.id}/rotate-token/`, { method: "POST" }), onSuccess: (c) => { setConfirmRotate(null); setCreated(c); refresh(); } });
  const shared = useMutation<unknown, ApiError, boolean>({ mutationFn: (v) => api(`/workspaces/${workspace.slug}/runner-settings/`, { method: "PATCH", json: { use_shared_runners: v } }), onSuccess: refresh });
  const rows = list.data?.results ?? [];
  const noDedicated = mode === "workspace" && list.data?.use_shared_runners === false && !rows.some((r) => r.status === "online");
  return (
    <>
      {mode === "workspace" && list.data && (
        <section className="card" aria-labelledby="rn-shared">
          <h2 id="rn-shared">Exécution des actions de cet espace</h2>
          <div className="field field-inline"><input type="checkbox" id="rn-use-shared" checked={list.data.use_shared_runners} disabled={shared.isPending} onChange={(e) => shared.mutate(e.target.checked)} /><label htmlFor="rn-use-shared">Autoriser les runners partagés et les workers intégrés à exécuter les actions de cet espace</label></div>
          <p className="hint">Décochez pour réserver l'exécution à vos runners dédiés (isolation, conformité, matériel particulier). {list.data.shared_online ?? 0} runner(s) partagé(s) en ligne actuellement.</p>
          {noDedicated && <p role="alert" className="field-error">Aucun runner dédié en ligne et les runners partagés sont désactivés : les actions de cet espace restent en attente.</p>}
          {shared.error && <p role="alert" className="field-error">Erreur : {errorText(shared.error)}</p>}
        </section>
      )}
      <div className="toolbar-row">
        <p className="hint" style={{ flex: 1 }}>{mode === "platform" ? "Les runners sont des agents installés sur des machines : ils appellent la plate-forme (jamais l'inverse), exécutent les actions dans des conteneurs et rendent le résultat. Partagés ou dédiés à des espaces." : "Runners dédiés à cet espace : ils n'exécutent que ses actions."}</p>
        <button type="button" className="primary" onClick={() => setEditing("new")}>Nouveau runner</button>
      </div>
      <div className="table-wrap"><table><caption className="sr-only">{mode === "platform" ? "Runners de la plate-forme" : "Runners dédiés à l'espace"}</caption>
        <thead><tr><th scope="col">Runner</th><th scope="col">Portée</th><th scope="col">État</th><th scope="col">Tâches</th><th scope="col">Étiquettes</th><th scope="col">Agent</th><th scope="col">Dernier contact</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td><strong>{r.name}</strong>{r.description && <><br /><span className="hint">{r.description}</span></>}</td>
              <td>{r.scope === "shared" ? "Partagé" : <>Dédié<br /><span className="hint mono">{r.workspaces.join(", ")}</span></>}</td>
              <td><span className={`runner-status ${STATUS[r.status][1]}`}>{STATUS[r.status][0]}</span></td>
              <td>{r.running_jobs} / {r.max_concurrent}</td>
              <td>{r.tags.length ? r.tags.map((t) => <span className="tag" key={t}>{t}</span>) : "—"}<br /><span className="hint">{r.runtimes.join(", ")}</span></td>
              <td>{r.agent_version ? <>v{r.agent_version}<br /><span className="hint">{r.platform}</span></> : "—"}</td>
              <td>{r.last_contact_at ? <>{formatDate(r.last_contact_at)}<br /><span className="hint mono">{r.last_ip}</span></> : "—"}</td>
              <td className="row-actions">
                {(mode === "platform" || r.managed) ? (
                  <>
                    <button type="button" className="icon-btn" onClick={() => pause.mutate(r)} aria-label={`${r.paused ? "Reprendre" : "Mettre en pause"} le runner ${r.name}`} title={r.paused ? "Reprendre" : "Mettre en pause"}><RowIcon name={r.paused ? "play" : "pause"} /></button>
                    <button type="button" className="icon-btn" onClick={() => setEditing(r)} aria-label={`Modifier le runner ${r.name}`} title="Modifier"><RowIcon name="edit" /></button>
                    <button type="button" className="icon-btn" onClick={() => setConfirmRotate(r)} aria-label={`Régénérer le jeton du runner ${r.name}`} title="Régénérer le jeton"><RowIcon name="key" /></button>
                    <button type="button" className="icon-btn danger-icon" onClick={() => setConfirmDelete(r)} aria-label={`Supprimer le runner ${r.name}`} title="Supprimer"><RowIcon name="trash" /></button>
                  </>
                ) : <span className="hint">Géré par la plate-forme</span>}
              </td>
            </tr>))}
          {list.data && rows.length === 0 && <tr><td colSpan={8} className="hint">Aucun runner. {mode === "workspace" ? "Sans runner dédié, les actions de cet espace sont exécutées par les workers de la plate-forme." : "Créez-en un pour exécuter des actions hors de la plate-forme."}</td></tr>}
        </tbody></table></div>
      {(pause.error || remove.error) && <p role="alert" className="field-error">Erreur : {errorText(pause.error ?? remove.error)}</p>}
      {editing && <RunnerForm mode={mode} runner={editing === "new" ? undefined : editing} spaces={spaces.data?.results ?? []} onClose={() => setEditing(null)} onCreated={(c) => { setEditing(null); setCreated(c); }} />}
      {created && <TokenView created={created} onClose={() => setCreated(null)} />}
      {confirmDelete && (
        <Overlay variant="modal" title={`Supprimer « ${confirmDelete.name} » ?`} onClose={() => setConfirmDelete(null)}
          footer={<><button type="button" onClick={() => setConfirmDelete(null)}>Annuler</button><button type="button" className="danger" disabled={remove.isPending} onClick={() => remove.mutate(confirmDelete)}>Supprimer le runner</button></>}>
          <p>L'agent ne pourra plus se connecter (son jeton est révoqué immédiatement). Une tâche qu'il exécute en ce moment sera reprise par un autre runner à l'expiration de son bail. L'historique des exécutions est conservé.</p>
        </Overlay>
      )}
      {confirmRotate && (
        <Overlay variant="modal" title={`Régénérer le jeton de « ${confirmRotate.name} » ?`} onClose={() => setConfirmRotate(null)}
          footer={<><button type="button" onClick={() => setConfirmRotate(null)}>Annuler</button><button type="button" className="primary" disabled={rotate.isPending} onClick={() => rotate.mutate(confirmRotate)}>Régénérer</button></>}>
          <p>L'ancien jeton cesse de fonctionner immédiatement : mettez à jour l'agent avec le nouveau jeton, affiché une seule fois.</p>
          {rotate.error && <p role="alert" className="field-error">Erreur : {errorText(rotate.error)}</p>}
        </Overlay>
      )}
    </>
  );
}
