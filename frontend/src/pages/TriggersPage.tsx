import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useLocation } from "react-router-dom";
import { apiAll, type WorkflowSummary } from "../api";
import { Badge, Confirm, EmptyRow, SearchBox, Section, usePaged } from "../components/AdminUi";
import { Overlay } from "../components/Overlay";
import { PageHeader } from "../components/PageHeader";
import { IconLabel } from "../forms/FormIcon";
import { RowIcon } from "../components/RowIcon";
import { Tabs } from "../components/Tabs";
import { useApp } from "../context";
import { formatDate } from "../i18n";
import { PollsTab } from "./triggers/PollsTab";
import { FormsTab } from "./triggers/FormsTab";
import { errorText, useWsMutation, useWsQuery } from "../lib";
import { HelpTip } from "../components/HelpTip";

interface Sched { id: number; name: string; workflow: string; environment: string; cron: string; timezone: string; enabled: boolean; overlap_policy: string; missed_policy: string; next_run_at: string | null; last_note: string; next_runs: string[] }
interface Hook { id: string; name: string; workflow: string; environment: string; enabled: boolean; url: string; provider: string; events: string[]; signing_secret?: { name: string; provider: string } | null }
const PROVIDERS: [string, string][] = [["fluxonata", "Appel signé par la plateforme (HMAC)"], ["github", "GitHub"], ["gitlab", "GitLab"], ["jira", "Jira"], ["token", "Jeton porteur (Authorization: Bearer)"]];
const PRESETS: [string, string][] = [["Toutes les heures", "0 * * * *"], ["Tous les jours à 08:00", "0 8 * * *"], ["Du lundi au vendredi à 09:00", "0 9 * * 1-5"], ["Toutes les 15 minutes", "*/15 * * * *"]];

export function TriggersPage() {
  const { can, workspace } = useApp();
  const wfs = useQuery({ queryKey: ["workflows", workspace.slug], queryFn: () => apiAll<WorkflowSummary>(`/workspaces/${workspace.slug}/workflows/`) });
  const sch = useWsQuery<{ results: Sched[] }>("schedules", "/schedules/");
  const hooks = useWsQuery<{ results: Hook[] }>("webhooks", "/webhooks/");
  const addS = useWsMutation<Record<string, unknown>>("POST", "/schedules/", ["schedules"]);
  const toggleS = useWsMutation<{ id: number; enabled: boolean }>("PATCH", (i) => `/schedules/${i.id}/`, ["schedules"]);
  const delS = useWsMutation<{ id: number }>("DELETE", (i) => `/schedules/${i.id}/`, ["schedules"]);
  const addH = useWsMutation<Record<string, unknown>, { secret: string; notice: string; url: string }>("POST", "/webhooks/", ["webhooks"]);
  const delH = useWsMutation<{ id: string }>("DELETE", (i) => `/webhooks/${i.id}/`, ["webhooks"]);
  const prefill = (useLocation().state as { workflow?: string } | null)?.workflow ?? "";
  const [tab, setTab] = useState("sch");
  const [openS, setOpenS] = useState(Boolean(prefill));
  const [openH, setOpenH] = useState(false);
  const [s, setS] = useState({ name: "", workflow: prefill, cron: "0 8 * * *", timezone: "Europe/Paris", environment: "test", inputs: "{}", overlap_policy: "skip", missed_policy: "skip" });
  const [h, setH] = useState({ name: "", workflow: "", environment: "test", provider: "fluxonata", events: "", secret: "" });
  const secretsList = useWsQuery<{ results: { name: string; provider: string }[] }>("secrets", "/secrets/", { enabled: can("manage_connections") });
  const [inputErr, setInputErr] = useState("");
  const publishable = (wfs.data?.results ?? []).filter((w) => w.latest_version);
  const edit = can("publish");
  const [qs, setQs] = useState("");
  const [qh, setQh] = useState("");
  const [askS, setAskS] = useState<Sched | null>(null);
  const [askH, setAskH] = useState<Hook | null>(null);
  const schRows = (sch.data?.results ?? []).filter((r) => `${r.name} ${r.workflow}`.toLowerCase().includes(qs.trim().toLowerCase()));
  const hookRows = (hooks.data?.results ?? []).filter((r) => `${r.name} ${r.workflow}`.toLowerCase().includes(qh.trim().toLowerCase()));
  const schPaged = usePaged(schRows, 10);
  const hookPaged = usePaged(hookRows, 10);
  return (
    <>
      <PageHeader title="Déclencheurs" description="Lancez un workflow à heure fixe (planification) ou depuis un système externe (webhook signé)." />
      <Tabs label="Types de déclencheurs" active={tab} onChange={setTab} tabs={[
        { id: "sch", label: "Planifications", content: (
          <>
            <p className="help-row"><HelpTip caption="À savoir">Les dates sont stockées en UTC et calculées dans le fuseau choisi. Une heure locale inexistante (passage à l'heure d'été) est sautée ; une heure répétée (retour à l'heure d'hiver) ne s'exécute qu'une fois.</HelpTip></p>
            <Section title="Planifications" info={`${sch.data?.results.filter((r) => r.enabled).length ?? 0} active(s) sur ${sch.data?.results.length ?? 0}.`}>
            <div className="adm-filters"><SearchBox label="Rechercher une planification" value={qs} onChange={setQs} placeholder="Nom ou workflow" />
              <span style={{ flex: 1 }} />
              {edit && <button type="button" className="primary" aria-haspopup="dialog" onClick={() => { addS.reset(); setOpenS(true); }}><IconLabel icon="plus">Nouvelle planification</IconLabel></button>}</div>
            <div className="table-wrap"><table className="adm-table"><caption className="sr-only">Planifications</caption>
              <thead><tr><th scope="col">Nom</th><th scope="col">Workflow</th><th scope="col">Quand</th><th scope="col">Prochaines exécutions</th><th scope="col">Statut</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>{schPaged.slice.map((r) => (
                <tr key={r.id}><td>{r.name}</td><td>{r.workflow} ({r.environment})</td><td><span className="mono">{r.cron}</span> — {r.timezone}</td>
                  <td>{r.next_runs.map((d) => formatDate(d)).join(" ; ") || "—"}</td><td>{r.enabled ? <Badge tone="ok">✔ Active</Badge> : <Badge tone="muted">⏸ Désactivée</Badge>}{r.last_note && <><br /><span className="hint">{r.last_note}</span></>}</td>
                  <td>{edit && <><button type="button" onClick={() => toggleS.mutate({ id: r.id, enabled: !r.enabled })}>{r.enabled ? "Désactiver" : "Activer"}</button>{" "}<button type="button" className="icon-btn danger-icon" onClick={() => setAskS(r)} aria-label={`Supprimer la planification ${r.name}`} title="Supprimer"><RowIcon name="trash" /></button></>}</td></tr>))}
                {schRows.length === 0 && <EmptyRow cols={6}>{sch.data?.results.length ? "Aucune planification ne correspond." : "Aucune planification."}</EmptyRow>}</tbody></table></div>
            {schPaged.pager("Pagination des planifications")}
            </Section>
          </>) },
        { id: "hooks", label: "Webhooks entrants", content: (
          <>
            <p className="help-row"><HelpTip caption="À savoir">Chaque appel doit être signé (HMAC-SHA256 de « horodatage.corps »), daté de moins de 5 minutes, et ne peut être rejoué. Rien n'est public par défaut.</HelpTip></p>
            <Section title="Webhooks entrants" info={`${hooks.data?.results.length ?? 0} webhook(s).`}>
            <div className="adm-filters"><SearchBox label="Rechercher un webhook" value={qh} onChange={setQh} placeholder="Nom ou workflow" />
              <span style={{ flex: 1 }} />
              {edit && <button type="button" className="primary" aria-haspopup="dialog" onClick={() => { addH.reset(); setOpenH(true); }}><IconLabel icon="plus">Nouveau webhook</IconLabel></button>}</div>
            <div className="table-wrap"><table className="adm-table"><caption className="sr-only">Webhooks entrants</caption>
              <thead><tr><th scope="col">Nom</th><th scope="col">Workflow</th><th scope="col">Adresse</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>{hookPaged.slice.map((r) => <tr key={r.id}><td>{r.name}{r.signing_secret && <> <Badge tone={r.signing_secret.provider === "vault" ? "info" : "muted"}>secret « {r.signing_secret.name} »{r.signing_secret.provider === "vault" ? " · Vault" : ""}</Badge></>}</td><td>{r.workflow} ({r.environment})</td><td><code>POST {r.url}</code>{r.provider !== "fluxonata" && <><br /><span className="hint">{PROVIDERS.find(([v]) => v === r.provider)?.[1]}{r.events.length ? ` — ${r.events.join(", ")}` : ""}</span></>}</td>
                <td>{edit && <button type="button" className="icon-btn danger-icon" onClick={() => setAskH(r)} aria-label={`Supprimer le webhook ${r.name}`} title="Supprimer"><RowIcon name="trash" /></button>}</td></tr>)}
                {hookRows.length === 0 && <EmptyRow cols={4}>{hooks.data?.results.length ? "Aucun webhook ne correspond." : "Aucun webhook."}</EmptyRow>}</tbody></table></div>
            {hookPaged.pager("Pagination des webhooks")}
            </Section>
          </>) },
        { id: "forms", label: "Formulaires publics", content: <FormsTab /> },
        { id: "polls", label: "Interrogation (service, e-mail)", content: <PollsTab /> },
      ]} />
      {askS && <Confirm title="Supprimer cette planification ?" confirmLabel="Supprimer la planification" busy={delS.isPending} onCancel={() => setAskS(null)} onConfirm={() => { delS.mutate({ id: askS.id }); setAskS(null); }}><p>La planification <strong>{askS.name}</strong> ne lancera plus <strong>{askS.workflow}</strong>. Les exécutions déjà faites sont conservées.</p></Confirm>}
      {askH && <Confirm title="Supprimer ce webhook ?" confirmLabel="Supprimer le webhook" busy={delH.isPending} onCancel={() => setAskH(null)} onConfirm={() => { delH.mutate({ id: askH.id }); setAskH(null); }}><p>Le webhook <strong>{askH.name}</strong> n'acceptera plus d'appels : les systèmes qui l'utilisent recevront une erreur.</p></Confirm>}
      {edit && openS && (
        <Overlay title="Nouvelle planification" description="Le workflow est lancé automatiquement selon l'expression cron."
          onClose={() => setOpenS(false)}
          footer={<><button type="button" onClick={() => setOpenS(false)}>Annuler</button><button type="submit" form="sched-form" className="primary">Créer la planification</button></>}>
          <form id="sched-form" onSubmit={(e) => { e.preventDefault(); let inputs; try { inputs = JSON.parse(s.inputs); setInputErr(""); } catch { setInputErr("Les entrées ne sont pas du JSON valide."); return; } addS.mutate({ ...s, inputs }, { onSuccess: () => setOpenS(false) }); }}>
          <div className="field"><label htmlFor="s-n">Nom</label><input id="s-n" value={s.name} onChange={(e) => setS({ ...s, name: e.target.value })} required /></div>
          <div className="field"><label htmlFor="s-w">Workflow</label><select id="s-w" value={s.workflow} onChange={(e) => setS({ ...s, workflow: e.target.value })} required><option value="">Choisir…</option>{publishable.map((w) => <option key={w.slug} value={w.slug}>{w.name}</option>)}</select></div>
          <div className="field"><label htmlFor="s-p">Fréquence prédéfinie</label><select id="s-p" value="" onChange={(e) => e.target.value && setS({ ...s, cron: e.target.value })}><option value="">Choisir un modèle…</option>{PRESETS.map(([l, c]) => <option key={c} value={c}>{l}</option>)}</select></div>
          <div className="field"><label htmlFor="s-c">Expression cron (minute heure jour mois jour-de-semaine)</label><input id="s-c" className="mono" value={s.cron} onChange={(e) => setS({ ...s, cron: e.target.value })} required /></div>
          <div className="field"><label htmlFor="s-t">Fuseau horaire</label><input id="s-t" value={s.timezone} onChange={(e) => setS({ ...s, timezone: e.target.value })} required /></div>
          <div className="field"><label htmlFor="s-e">Environnement</label><select id="s-e" value={s.environment} onChange={(e) => setS({ ...s, environment: e.target.value })}><option value="test">Recette</option><option value="prod">Production</option></select></div>
          <div className="field"><label htmlFor="s-i">Entrées du workflow (JSON)</label><input id="s-i" className="mono" value={s.inputs} onChange={(e) => setS({ ...s, inputs: e.target.value })} /></div>
          <div className="field"><label htmlFor="s-o">Si l'exécution précédente tourne encore</label><select id="s-o" value={s.overlap_policy} onChange={(e) => setS({ ...s, overlap_policy: e.target.value })}><option value="skip">Ignorer cette occurrence</option><option value="allow">Lancer quand même</option></select></div>
          <div className="field"><label htmlFor="s-m">Occurrences manquées (service arrêté)</label><select id="s-m" value={s.missed_policy} onChange={(e) => setS({ ...s, missed_policy: e.target.value })}><option value="skip">Les ignorer</option><option value="run_once">Rattraper une seule fois</option></select></div>
            {inputErr && <p role="alert" className="field-error">Erreur : {inputErr}</p>}
            {addS.error && <p role="alert" className="field-error">Erreur : {errorText(addS.error)}</p>}
          </form>
        </Overlay>
      )}
      {edit && openH && (
        <Overlay title="Nouveau webhook" description="Un secret de signature est généré : il ne sera affiché qu'une seule fois."
          onClose={() => setOpenH(false)}
          footer={addH.data ? <button type="button" className="primary" onClick={() => setOpenH(false)}>J'ai noté le secret</button> : <><button type="button" onClick={() => setOpenH(false)}>Annuler</button><button type="submit" form="hook-form" className="primary">Créer le webhook</button></>}>
          {addH.data ? (
            <div role="alert" className="onetime"><strong>{addH.data.notice}</strong><br />Secret : <code>{addH.data.secret}</code><br />URL : <code>{addH.data.url}</code></div>
          ) : (
            <form id="hook-form" onSubmit={(e) => { e.preventDefault(); addH.mutate({ ...h, secret: h.secret || undefined, events: h.events.split(",").map((x) => x.trim()).filter(Boolean) }); }}>
          <div className="field"><label htmlFor="h-n">Nom</label><input id="h-n" value={h.name} onChange={(e) => setH({ ...h, name: e.target.value })} required /></div>
          <div className="field"><label htmlFor="h-w">Workflow</label><select id="h-w" value={h.workflow} onChange={(e) => setH({ ...h, workflow: e.target.value })} required><option value="">Choisir…</option>{publishable.map((w) => <option key={w.slug} value={w.slug}>{w.name}</option>)}</select></div>
          <div className="field"><label htmlFor="h-p">Origine des appels</label><select id="h-p" value={h.provider} onChange={(e) => setH({ ...h, provider: e.target.value })}>{PROVIDERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
            {h.provider !== "fluxonata" && <p className="hint">Le workflow doit déclarer une entrée « payload » (objet) ; « event » et « delivery » (texte) sont fournies si elles existent. Collez le secret affiché après création dans la configuration du webhook chez le fournisseur.</p>}</div>
          {h.provider !== "fluxonata" && <div className="field"><label htmlFor="h-ev">Événements acceptés (séparés par des virgules, vide = tous)</label><input id="h-ev" value={h.events} placeholder="push, pull_request" onChange={(e) => setH({ ...h, events: e.target.value })} /></div>}
          {(secretsList.data?.results.length ?? 0) > 0 && (
            <div className="field"><label htmlFor="h-sec">Secret de signature</label>
              <select id="h-sec" value={h.secret} onChange={(e) => setH({ ...h, secret: e.target.value })}>
                <option value="">Généré par Fluxonata (affiché une seule fois)</option>
                {secretsList.data?.results.map((x) => <option key={x.name} value={x.name}>{x.name} — {x.provider === "vault" ? "dans Vault" : "coffre de Fluxonata"}</option>)}
              </select>
              <HelpTip>Utilisez un secret de l'espace (par exemple conservé dans Vault) pour vérifier les appels : Fluxonata ne le génère ni ne l'affiche. Il se remplace dans Administration → Secrets.</HelpTip></div>
          )}
          <div className="field"><label htmlFor="h-e">Environnement</label><select id="h-e" value={h.environment} onChange={(e) => setH({ ...h, environment: e.target.value })}><option value="test">Recette</option><option value="prod">Production</option></select></div>
              {addH.error && <p role="alert" className="field-error">Erreur : {errorText(addH.error)}</p>}
            </form>
          )}
        </Overlay>
      )}
    </>
  );
}
