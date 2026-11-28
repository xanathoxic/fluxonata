import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { Page, WorkflowRun } from "../../api";
import { Badge, Section } from "../../components/AdminUi";
import { StatusBadge } from "../../components/StatusBadge";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { errorText, useWsMutation, useWsQuery } from "../../lib";
import { Tabs } from "../../components/Tabs";
import { Kpi } from "../../pages/OpsCharts";
import { useDraft } from "../DraftContext";
import { HelpTip } from "../../components/HelpTip";
import { TagInput } from "../../components/Tags";

interface RunnerChoice {
  id: string;
  name: string;
  description: string;
  scope: string;
  tags: string[];
  runtimes: string[];
  status: string;
}
const STATUS: Record<string, string> = { online: "en ligne", offline: "hors ligne", paused: "en pause", never: "jamais connecté" };

/** Runner imposé au workflow, et extension à ses sous-workflows. Sans choix : le runner est choisi automatiquement parmi les runners compatibles. */
function RunnerSettings({
  value,
  onChange,
  force,
  onForce,
  choices,
  current,
}: {
  value: string;
  onChange: (v: string) => void;
  force: boolean;
  onForce: (v: boolean) => void;
  choices: RunnerChoice[];
  current: string | null;
}) {
  const known = value === "" || choices.some((c) => c.id === value);
  const picked = choices.find((c) => c.id === value);
  return (
    <>
      <div className="field">
        <HelpTip>
          Les actions et scripts de ce workflow ne s'exécutent alors que sur ce runner, jamais sur un autre. S'il est hors ligne ou en pause, ils l'attendent ;
          s'il ne peut pas exécuter une étape (langage, étiquette), l'étape échoue avec un message clair. Les étapes IA, HTTP, SQL, LDAP, SMTP, SFTP et SSH
          restent sur la plateforme.
        </HelpTip>
        <label htmlFor="sum-runner">Runner imposé</label>
        <select id="sum-runner" value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">Choix automatique (aucun runner imposé)</option>
          {choices.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} — {c.scope === "dedicated" ? "dédié" : "partagé"}, {STATUS[c.status] ?? c.status}
            </option>
          ))}
          {!known && <option value={value}>{current ?? value} (non disponible pour cet espace)</option>}
        </select>
        {picked && (
          <p className="hint">
            Langages : {picked.runtimes.join(", ") || "aucun"}
            {picked.tags.length > 0 ? ` · étiquettes : ${picked.tags.join(", ")}` : ""}
          </p>
        )}
        {!known && (
          <p role="alert" className="field-error">
            Ce runner n'est plus utilisable par cet espace : choisissez-en un autre ou retirez-le.
          </p>
        )}
      </div>
      <div className="adm-rule">
        <input id="sum-force" className="adm-switch" type="checkbox" checked={force} disabled={value === ""} onChange={(e) => onForce(e.target.checked)} />
        <div>
          <label htmlFor="sum-force">Imposer ce runner aux sous-workflows</label>
          <HelpTip>
            Si l'option est active, les sous-workflows (étape Workflow, boucle sur liste, boucle conditionnelle), et leurs propres sous-workflows, s'exécutent
            aussi sur ce runner, même si un autre runner leur a été assigné. Sans elle, chaque sous-workflow garde son propre runner, ou le choix automatique.
          </HelpTip>
        </div>
      </div>
    </>
  );
}

export function SummaryTab() {
  const d = useDraft();
  const { can } = useApp();
  const meta = useWsMutation<{
    description: string;
    folder: string;
    tags: string[];
    error_workflow: string;
    success_workflow: string;
    mcp_exposed: boolean;
    runner: string;
    runner_force_children: boolean;
    run_mode: string;
    cache_key: string;
    cache_ttl_seconds: number | null;
    cache_sensitive: boolean;
  }>("PATCH", `/workflows/${d.slug}/meta/`, ["workflows"]);
  const runs = useWsQuery<Page<WorkflowRun>>(`wf-runs-${d.slug}`, `/workflow-runs/?workflow=${d.slug}`);
  const [description, setDescription] = useState(d.wf.description);
  const [folder, setFolder] = useState(d.wf.folder);
  const [errorWf, setErrorWf] = useState(d.wf.error_workflow ?? "");
  const [okWf, setOkWf] = useState(d.wf.success_workflow ?? "");
  const [mcp, setMcp] = useState(d.wf.mcp_exposed ?? false);
  const [runner, setRunner] = useState(d.wf.runner ?? "");
  const [forceChildren, setForceChildren] = useState(d.wf.runner_force_children ?? false);
  const [runMode, setRunMode] = useState(d.wf.run_mode ?? "concurrent");
  const [sumTab, setSumTab] = useState("info");
  const [cacheSensitive, setCacheSensitive] = useState(d.wf.cache_sensitive ?? false);
  const [cacheKey, setCacheKey] = useState(d.wf.cache_key ?? "");
  const [cacheTtl, setCacheTtl] = useState(d.wf.cache_ttl_seconds ? String(d.wf.cache_ttl_seconds) : "");
  const runners = useWsQuery<{ results: RunnerChoice[] }>("runner-choices", "/runner-choices/", { enabled: can("edit") });
  const [tags, setTags] = useState<string[]>(d.wf.tags ?? []);
  const all = useWsQuery<Page<{ slug: string; name: string; tags?: string[] }>>("workflows-all", "/workflows/?limit=200");
  useEffect(() => {
    setDescription(d.wf.description);
    setFolder(d.wf.folder);
    setErrorWf(d.wf.error_workflow ?? "");
    setOkWf(d.wf.success_workflow ?? "");
    setMcp(d.wf.mcp_exposed ?? false);
    setRunner(d.wf.runner ?? "");
    setForceChildren(d.wf.runner_force_children ?? false);
    setRunMode(d.wf.run_mode ?? "concurrent");
    setCacheKey(d.wf.cache_key ?? "");
    setCacheSensitive(d.wf.cache_sensitive ?? false);
    setCacheTtl(d.wf.cache_ttl_seconds ? String(d.wf.cache_ttl_seconds) : "");
    setTags(d.wf.tags ?? []);
  }, [
    d.wf.run_mode,
    d.wf.cache_sensitive,
    d.wf.cache_key,
    d.wf.cache_ttl_seconds,
    d.wf.runner,
    d.wf.runner_force_children,
    d.wf.description,
    d.wf.folder,
    d.wf.error_workflow,
    d.wf.success_workflow,
    d.wf.mcp_exposed,
    d.wf.tags,
  ]);
  const editable = can("edit");
  const { workspace } = useApp();
  const nodes = d.graph.nodes.length;
  const v = d.wf.latest_version;
  const inputs = Object.keys(d.graph.inputs.properties ?? {}).length;
  return (
    <section aria-label="Résumé du workflow" className="sum-page">
      <h2 className="sr-only">Résumé</h2>
      <ul className="kpis" aria-label="Chiffres du workflow">
        <Kpi label="Version publiée" value={v ? `v${v.version}` : "Aucune"} hint={v ? formatDate(v.created_at) : "jamais publié"} tone={v ? "ok" : undefined} />
        <Kpi label="Étapes" value={nodes} hint="dans le brouillon" />
        <Kpi label="Entrées" value={inputs} hint={`${(d.graph.variables ?? []).length} variable(s)`} />
        <Kpi label="Dernière modification" value={formatDate(d.wf.updated_at)} hint={`propriétaire : ${d.wf.owner ?? "—"}`} tone="info" />
      </ul>
      {d.wf.deprecated && (
        <p role="note" className="notice">
          ⚠ Déprécié : {d.wf.deprecation_reason || "aucun motif"}.
        </p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          meta.mutate(
            {
              description,
              folder,
              tags,
              error_workflow: errorWf,
              success_workflow: okWf,
              mcp_exposed: mcp,
              runner,
              runner_force_children: forceChildren && runner !== "",
              run_mode: runMode,
              cache_key: cacheKey.trim(),
              cache_sensitive: cacheSensitive,
              cache_ttl_seconds: /^\d+$/.test(cacheTtl.trim()) ? Number(cacheTtl.trim()) : cacheTtl.trim() === "" ? null : (cacheTtl as unknown as number),
            },
            { onSuccess: () => void d.refetchWorkflow() },
          );
        }}
      >
        <Tabs
          label="Réglages du workflow"
          active={sumTab}
          onChange={setSumTab}
          tabs={[
            {
              id: "info",
              label: "Informations",
              content: (
                <fieldset disabled={!editable} className="bare sum-stack-fs">
                  <div className="sum-grid sum-stack">
                    <Section title="Informations" hint="Ce que fait le workflow et où il se range.">
                      <TagInput
                        value={tags}
                        onChange={setTags}
                        suggestions={[...new Set((all.data?.results ?? []).flatMap((w) => w.tags ?? []))].sort()}
                        disabled={!editable}
                      />
                      <div className="field">
                        <label htmlFor="sum-desc">Description</label>
                        <textarea id="sum-desc" rows={4} value={description} onChange={(e) => setDescription(e.target.value)} />
                      </div>
                      <div className="field">
                        <HelpTip>Les dossiers organisent l'arbre des workflows (séparez les niveaux par « / »).</HelpTip>
                        <label htmlFor="sum-folder">Dossier</label>
                        <input id="sum-folder" value={folder} placeholder="ex. Réseau/DNS" onChange={(e) => setFolder(e.target.value)} />
                      </div>
                    </Section>
                    <Section title="Intégration" hint="Rendre ce workflow appelable par un assistant.">
                      <div className="adm-rule">
                        <input id="sum-mcp" className="adm-switch" type="checkbox" checked={mcp} onChange={(e) => setMcp(e.target.checked)} />
                        <div>
                          <label htmlFor="sum-mcp">Exposer comme outil MCP</label>
                          <HelpTip>
                            Un assistant ou un agent compatible MCP peut alors appeler ce workflow (dernière version publiée, environnement de recette) avec un
                            jeton de service : adresse{" "}
                            <code>
                              {window.location.origin}/api/v1/workspaces/{workspace.slug}/mcp/
                            </code>
                            . L'appel suit les mêmes droits, politiques et audit qu'un lancement.
                          </HelpTip>
                        </div>
                      </div>
                    </Section>
                    <Section
                      title="Dernières exécutions"
                      actions={
                        <Link className="button" to={`/workflows/${d.slug}/runs`}>
                          Voir tout
                        </Link>
                      }
                    >
                      <ul className="plain home-runs">
                        {runs.data?.results.slice(0, 5).map((r) => (
                          <li key={r.id}>
                            <StatusBadge status={r.status} label={r.status_label} />{" "}
                            <Link className="lst-title" to={`/runs/${r.id}`}>
                              {formatDate(r.created_at)}
                            </Link>{" "}
                            <span className="hint">
                              v{r.workflow.version} · <Badge>{r.environment}</Badge>
                            </span>
                          </li>
                        ))}
                        {runs.data?.results.length === 0 && <li className="hint">Aucune exécution.</li>}
                      </ul>
                    </Section>
                  </div>
                </fieldset>
              ),
            },
            {
              id: "chain",
              label: "Enchaînements",
              content: (
                <fieldset disabled={!editable} className="bare sum-stack-fs">
                  <div className="sum-grid sum-stack sum-one">
                    <Section title="Enchaînements" hint="Des workflows lancés automatiquement selon le résultat.">
                      <div className="field">
                        <HelpTip>
                          Lancé automatiquement quand une exécution de ce workflow échoue, avec en entrées (si elles sont déclarées) : workflow, workflow_name,
                          run_id, environment, step, code, message. Il doit avoir une version publiée.
                        </HelpTip>
                        <label htmlFor="sum-err">Workflow d'erreur</label>
                        <select id="sum-err" value={errorWf} onChange={(e) => setErrorWf(e.target.value)}>
                          <option value="">Aucun</option>
                          {all.data?.results
                            .filter((w) => w.slug !== d.slug)
                            .map((w) => (
                              <option key={w.slug} value={w.slug}>
                                {w.name}
                              </option>
                            ))}
                        </select>
                      </div>
                      <div className="field">
                        <HelpTip>
                          Lancé automatiquement quand une exécution réussit, avec en entrées (si elles sont déclarées) : workflow, workflow_name, run_id,
                          environment, outputs (les sorties du workflow). Ne se chaîne jamais.
                        </HelpTip>
                        <label htmlFor="sum-ok">Workflow de succès</label>
                        <select id="sum-ok" value={okWf} onChange={(e) => setOkWf(e.target.value)}>
                          <option value="">Aucun</option>
                          {all.data?.results
                            .filter((w) => w.slug !== d.slug)
                            .map((w) => (
                              <option key={w.slug} value={w.slug}>
                                {w.name}
                              </option>
                            ))}
                        </select>
                      </div>
                    </Section>
                  </div>
                </fieldset>
              ),
            },
            {
              id: "env",
              label: "Environnement",
              content: (
                <fieldset disabled={!editable} className="bare sum-stack-fs">
                  <div className="sum-grid sum-stack">
                    <Section title="Exécution" hint="Où et comment s'exécute ce workflow.">
                      <RunnerSettings
                        value={runner}
                        onChange={(v) => {
                          setRunner(v);
                          if (!v) setForceChildren(false);
                        }}
                        force={forceChildren}
                        onForce={setForceChildren}
                        choices={runners.data?.results ?? []}
                        current={d.wf.runner_name ?? null}
                      />
                      <div className="adm-rule">
                        <input
                          id="sum-run-mode"
                          className="adm-switch"
                          type="checkbox"
                          checked={runMode === "sequential"}
                          onChange={(e) => setRunMode(e.target.checked ? "sequential" : "concurrent")}
                        />
                        <div>
                          <label htmlFor="sum-run-mode">Exécution séquentielle</label>
                          <HelpTip>
                            Désactivé (par défaut), le mode est concurrent : chaque lancement démarre tout de suite et plusieurs peuvent s'exécuter en même
                            temps. Activé, le mode est séquentiel : un seul lancement à la fois (par environnement) ; les suivants, depuis le catalogue ou
                            ailleurs, restent « En attente » et démarrent dans l'ordre d'arrivée dès que le précédent est terminé, réussi ou non.
                          </HelpTip>
                        </div>
                      </div>
                    </Section>
                    <Section title="Cache" hint="Ce que ce workflow laisse aux autres workflows.">
                      <p className="hint">
                        Partagez le résultat de ce workflow avec d'autres workflows : ils le relisent plus tard avec l'étape « Cache », sans relancer le calcul.
                      </p>
                      <div className="field">
                        <HelpTip>
                          Si une clé est indiquée, les sorties de chaque exécution réussie sont écrites dans le cache de l'espace (environnement de l'exécution)
                          sous cette clé ; un autre workflow les relit avec l'étape « Cache ». Une valeur trop grande ou un cache plein n'échoue jamais
                          l'exécution. N'y exposez aucun secret.
                        </HelpTip>
                        <label htmlFor="sum-cache-key">Publier le résultat dans le cache (clé)</label>
                        <input
                          id="sum-cache-key"
                          value={cacheKey}
                          placeholder="inventaire-serveurs (vide : pas de publication)"
                          onChange={(e) => setCacheKey(e.target.value)}
                        />
                      </div>
                      {cacheKey.trim() !== "" && (
                        <div className="field">
                          <HelpTip>Au-delà de ce délai, la valeur est considérée absente. Vide : elle reste jusqu'à la prochaine publication.</HelpTip>
                          <label htmlFor="sum-cache-ttl">Durée de vie du résultat (secondes, facultatif)</label>
                          <input
                            id="sum-cache-ttl"
                            inputMode="numeric"
                            value={cacheTtl}
                            placeholder="21600 (6 h)"
                            onChange={(e) => setCacheTtl(e.target.value)}
                          />
                        </div>
                      )}
                      {cacheKey.trim() !== "" && (
                        <div className="adm-rule">
                          <input
                            id="sum-cache-sensitive"
                            className="adm-switch"
                            type="checkbox"
                            checked={cacheSensitive}
                            onChange={(e) => setCacheSensitive(e.target.checked)}
                          />
                          <div>
                            <label htmlFor="sum-cache-sensitive">Résultat sensible</label>
                            <HelpTip>
                              Le résultat publié n'est visible (page « Caches » et API) que des administrateurs de l'espace. Les workflows peuvent toujours le
                              lire : ce n'est pas un coffre pour des secrets.
                            </HelpTip>
                          </div>
                        </div>
                      )}
                    </Section>
                  </div>
                </fieldset>
              ),
            },
          ]}
        />
        <fieldset disabled={!editable} className="bare">
          <div className="adm-savebar">
            <button type="submit" className="primary" disabled={meta.isPending}>
              Enregistrer
            </button>
            {meta.isSuccess && (
              <span role="status" className="ok-text">
                Enregistré.
              </span>
            )}
            {meta.error && (
              <p role="alert" className="field-error">
                Erreur : {errorText(meta.error)}
              </p>
            )}
          </div>
        </fieldset>
      </form>
    </section>
  );
}
