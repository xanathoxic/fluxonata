import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useLocation } from "react-router-dom";
import { apiAll, type WorkflowSummary } from "../api";
import { Overlay } from "../components/Overlay";
import { PageHeader } from "../components/PageHeader";
import { RowIcon } from "../components/RowIcon";
import { Tabs } from "../components/Tabs";
import { useApp } from "../context";
import { formatDate } from "../i18n";
import { errorText, useWsMutation, useWsQuery } from "../lib";

interface Sched { id: number; name: string; workflow: string; environment: string; cron: string; timezone: string; enabled: boolean; overlap_policy: string; missed_policy: string; next_run_at: string | null; last_note: string; next_runs: string[] }
interface Hook { id: string; name: string; workflow: string; environment: string; enabled: boolean; url: string }
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
  const [h, setH] = useState({ name: "", workflow: "", environment: "test" });
  const [inputErr, setInputErr] = useState("");
  const publishable = (wfs.data?.results ?? []).filter((w) => w.latest_version);
  const edit = can("publish");
  return (
    <>
      <PageHeader title="Déclencheurs" description="Lancez un workflow à heure fixe (planification) ou depuis un système externe (webhook signé)."
        actions={edit && (tab === "sch"
          ? <button type="button" className="primary" onClick={() => { addS.reset(); setOpenS(true); }}>Nouvelle planification</button>
          : <button type="button" className="primary" onClick={() => { addH.reset(); setOpenH(true); }}>Nouveau webhook</button>)} />
      <Tabs label="Types de déclencheurs" active={tab} onChange={setTab} tabs={[
        { id: "sch", label: "Planifications", content: (
          <>
            <p className="hint">Les dates sont stockées en UTC et calculées dans le fuseau choisi. Une heure locale inexistante (passage à l'heure d'été) est sautée ; une heure répétée (retour à l'heure d'hiver) ne s'exécute qu'une fois.</p>
            <div className="table-wrap"><table><caption className="sr-only">Planifications</caption>
              <thead><tr><th scope="col">Nom</th><th scope="col">Workflow</th><th scope="col">Quand</th><th scope="col">Prochaines exécutions</th><th scope="col">Statut</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>{sch.data?.results.map((r) => (
                <tr key={r.id}><td>{r.name}</td><td>{r.workflow} ({r.environment})</td><td><span className="mono">{r.cron}</span> — {r.timezone}</td>
                  <td>{r.next_runs.map((d) => formatDate(d)).join(" ; ") || "—"}</td><td>{r.enabled ? "✔ Active" : "⏸ Désactivée"}{r.last_note && <><br /><span className="hint">{r.last_note}</span></>}</td>
                  <td>{edit && <><button type="button" onClick={() => toggleS.mutate({ id: r.id, enabled: !r.enabled })}>{r.enabled ? "Désactiver" : "Activer"}</button>{" "}<button type="button" className="icon-btn danger-icon" onClick={() => delS.mutate({ id: r.id })} aria-label={`Supprimer la planification ${r.name}`} title="Supprimer"><RowIcon name="trash" /></button></>}</td></tr>))}
                {sch.data?.results.length === 0 && <tr><td colSpan={6} className="hint">Aucune planification.</td></tr>}</tbody></table></div>
          </>) },
        { id: "hooks", label: "Webhooks entrants", content: (
          <>
            <p className="hint">Chaque appel doit être signé (HMAC-SHA256 de « horodatage.corps »), daté de moins de 5 minutes, et ne peut être rejoué. Rien n'est public par défaut.</p>
            <div className="table-wrap"><table><caption className="sr-only">Webhooks entrants</caption>
              <thead><tr><th scope="col">Nom</th><th scope="col">Workflow</th><th scope="col">Adresse</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>{hooks.data?.results.map((r) => <tr key={r.id}><td>{r.name}</td><td>{r.workflow} ({r.environment})</td><td><code>POST {r.url}</code></td>
                <td>{edit && <button type="button" className="icon-btn danger-icon" onClick={() => delH.mutate({ id: r.id })} aria-label={`Supprimer le webhook ${r.name}`} title="Supprimer"><RowIcon name="trash" /></button>}</td></tr>)}
                {hooks.data?.results.length === 0 && <tr><td colSpan={4} className="hint">Aucun webhook.</td></tr>}</tbody></table></div>
          </>) },
      ]} />
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
            <form id="hook-form" onSubmit={(e) => { e.preventDefault(); addH.mutate(h); }}>
          <div className="field"><label htmlFor="h-n">Nom</label><input id="h-n" value={h.name} onChange={(e) => setH({ ...h, name: e.target.value })} required /></div>
          <div className="field"><label htmlFor="h-w">Workflow</label><select id="h-w" value={h.workflow} onChange={(e) => setH({ ...h, workflow: e.target.value })} required><option value="">Choisir…</option>{publishable.map((w) => <option key={w.slug} value={w.slug}>{w.name}</option>)}</select></div>
          <div className="field"><label htmlFor="h-e">Environnement</label><select id="h-e" value={h.environment} onChange={(e) => setH({ ...h, environment: e.target.value })}><option value="test">Recette</option><option value="prod">Production</option></select></div>
              {addH.error && <p role="alert" className="field-error">Erreur : {errorText(addH.error)}</p>}
            </form>
          )}
        </Overlay>
      )}
    </>
  );
}
