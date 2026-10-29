import { useId, useState } from "react";
import { Overlay } from "../components/Overlay";
import { PageHeader } from "../components/PageHeader";
import { Tabs } from "../components/Tabs";
import { useApp } from "../context";
import { formatDate } from "../i18n";
import { errorText, useWsMutation, useWsQuery } from "../lib";
import { HBars, Kpi, RunsChart } from "./OpsCharts";
import { HelpTip } from "../components/HelpTip";

interface Dash {
  period_days: number;
  totals: { runs: number; succeeded: number; failed: number; cancelled: number; running: number; failure_rate: number | null };
  per_day: { day: string; succeeded: number; failed: number }[];
  durations: { p50_s: number | null; p95_s: number | null };
  top_failing: { workflow: string; name: string; runs: number; failed: number }[];
  slowest: { workflow: string; name: string; runs: number; p95_s: number | null }[];
  failing_steps: { workflow: string; step: string; count: number }[];
  queue: { running_runs: number; queued_executions: number; waiting_steps: number };
  ai: { tokens: number; calls: number };
}
interface Rule { id: number; name: string; event: string; enabled: boolean; environment: string; threshold_percent: number; min_runs: number; window_minutes: number; destination: string; format: string; last_fired_at: string | null }
const sec = (v: number | null) => (v === null ? "—" : v < 60 ? `${v} s` : `${Math.round(v / 6) / 10} min`);
const ENV: Record<string, string> = { prod: "Production", test: "Recette", dev: "Développement", "": "Tous les environnements" };
const EMPTY = { name: "", event: "run_failed", environment: "prod", threshold_percent: 30, min_runs: 10, url: "", format: "slack" };

function Card({ title, hint, children, id }: { title: string; hint?: string; children: React.ReactNode; id: string }) {
  return <section className="ops-card" aria-labelledby={id}><h2 id={id}>{title}</h2>{hint && <p className="hint">{hint}</p>}{children}</section>;
}

function Overview({ d }: { d: Dash }) {
  const rate = d.totals.failure_rate;
  const finished = d.totals.succeeded + d.totals.failed;
  return (
    <>
      <ul className="kpis" aria-label="Chiffres clés">
        <Kpi label="Exécutions" value={d.totals.runs} hint={`${finished} terminée(s) · ${d.totals.cancelled} annulée(s)`} />
        <Kpi label="Taux d'échec" value={rate === null ? "—" : `${rate} %`} tone={rate === null ? undefined : rate >= 20 ? "bad" : "ok"} share={rate === null ? null : 100 - rate} hint={`${d.totals.failed} échec(s) · ${rate === null ? "aucune exécution terminée" : `${100 - rate} % de réussite`}`} />
        <Kpi label="Durée médiane" value={sec(d.durations.p50_s)} hint={`95e centile : ${sec(d.durations.p95_s)}`} />
        <Kpi label="En cours" value={d.totals.running} tone="info" hint={`${d.queue.queued_executions} en file · ${d.queue.waiting_steps} en attente`} />
        <Kpi label="Assistant IA" value={d.ai.tokens.toLocaleString("fr-FR")} hint={`jetons · ${d.ai.calls} appel(s)`} />
      </ul>
      <Card id="ops-day" title="Exécutions par jour">
        {d.per_day.length === 0 ? <p className="hint viz-empty">Aucune exécution terminée sur la période.</p> : <RunsChart data={d.per_day} />}
      </Card>
      <div className="ops-grid">
        <Card id="ops-tf" title="Workflows qui échouent le plus">
          <HBars label="Workflows qui échouent le plus" empty="Aucun échec sur la période." rows={d.top_failing.slice(0, 5).map((r) => ({ key: r.workflow, label: r.name, value: r.failed, shown: `${r.failed} / ${r.runs}`, tone: "bad" as const, sub: `${Math.round((100 * r.failed) / Math.max(1, r.runs))} %` }))} />
        </Card>
        <Card id="ops-fs" title="Étapes en échec">
          <HBars label="Étapes en échec" empty="Aucune étape en échec." rows={d.failing_steps.slice(0, 5).map((r) => ({ key: `${r.workflow}/${r.step}`, label: `« ${r.step} »`, sub: r.workflow, value: r.count, shown: String(r.count), tone: "bad" as const }))} />
        </Card>
        <Card id="ops-sl" title="Les plus lents (95e centile)">
          <HBars label="Les plus lents" empty="Pas encore de durée mesurée." rows={d.slowest.slice(0, 5).map((r) => ({ key: r.workflow, label: r.name, value: r.p95_s ?? 0, shown: sec(r.p95_s), sub: `${r.runs} exéc.` }))} />
        </Card>
      </div>
    </>
  );
}

function RuleForm({ id, f, setF }: { id: string; f: typeof EMPTY; setF: (v: typeof EMPTY) => void }) {
  return (
    <>
      <div className="field"><label htmlFor={`${id}-n`}>Nom</label><input id={`${id}-n`} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required maxLength={80} placeholder="Échecs de production" /></div>
      <div className="field"><label htmlFor={`${id}-e`}>Déclencheur</label><select id={`${id}-e`} value={f.event} onChange={(e) => setF({ ...f, event: e.target.value })}><option value="run_failed">Une exécution échoue</option><option value="failure_rate">Le taux d'échec dépasse un seuil</option></select></div>
      {f.event === "failure_rate" && <div className="field">
<HelpTip>Mesuré sur les {f.min_runs} exécutions terminées au moins de la dernière heure.</HelpTip><label htmlFor={`${id}-t`}>Seuil d'échec (%)</label><input id={`${id}-t`} type="number" min={1} max={100} value={f.threshold_percent} onChange={(e) => setF({ ...f, threshold_percent: Number(e.target.value) })} /></div>}
      <div className="field"><label htmlFor={`${id}-env`}>Environnement</label><select id={`${id}-env`} value={f.environment} onChange={(e) => setF({ ...f, environment: e.target.value })}><option value="">Tous</option><option value="prod">Production</option><option value="test">Recette</option><option value="dev">Développement</option></select></div>
      <div className="field"><label htmlFor={`${id}-f`}>Format du message</label><select id={`${id}-f`} value={f.format} onChange={(e) => setF({ ...f, format: e.target.value })}><option value="slack">Slack ou compatible ({"{"}"text": …{"}"})</option><option value="json">JSON complet</option></select></div>
      <div className="field">
<HelpTip>Traitée comme un secret : chiffrée, jamais réaffichée en entier.</HelpTip><label htmlFor={`${id}-u`}>Adresse de destination</label><input id={`${id}-u`} type="url" autoComplete="off" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} required placeholder="https://hooks.exemple.org/…" /></div>
    </>
  );
}

function Alerts() {
  const fid = useId();
  const q = useWsQuery<{ results: Rule[] }>("ops-alerts", "/ops/alerts/");
  const add = useWsMutation<Record<string, unknown>>("POST", "/ops/alerts/", ["ops-alerts"]);
  const patch = useWsMutation<{ id: number; enabled: boolean }>("PATCH", (i) => `/ops/alerts/${i.id}/`, ["ops-alerts"]);
  const del = useWsMutation<{ id: number }>("DELETE", (i) => `/ops/alerts/${i.id}/`, ["ops-alerts"]);
  const test = useWsMutation<{ id: number }>("POST", (i) => `/ops/alerts/${i.id}/test/`);
  const [creating, setCreating] = useState(false);
  const [confirm, setConfirm] = useState<Rule | null>(null);
  const [f, setF] = useState(EMPTY);
  const rules = q.data?.results ?? [];
  const open = () => { add.reset(); setF(EMPTY); setCreating(true); };
  return (
    <>
      <div className="toolbar-row ops-bar">
        <span style={{ flex: 1 }}><HelpTip caption="À savoir">Prévient un canal (Slack ou tout service qui reçoit un POST JSON) quand une exécution échoue ou que le taux d'échec dépasse un seuil.</HelpTip></span>
        <button type="button" className="primary" onClick={open}>Nouvelle règle</button>
      </div>
      {test.isSuccess && <p role="status" className="ops-note">Test mis en file : le travailleur l'envoie dans quelques secondes.</p>}
      {rules.length === 0 ? (
        <div className="ops-empty"><strong>Aucune règle d'alerte</strong><p className="hint">Créez une règle pour être prévenu dès qu'une exécution de production échoue.</p><button type="button" className="primary" onClick={open}>Créer la première règle</button></div>
      ) : (
        <div className="table-wrap"><table><caption className="sr-only">Règles d'alerte</caption>
          <thead><tr><th scope="col">Règle</th><th scope="col">Quand</th><th scope="col">Destination</th><th scope="col">Dernier envoi</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{rules.map((r) => (
            <tr key={r.id}>
              <td><strong>{r.name}</strong><br /><span className={`chip ${r.enabled ? "chip-ok" : ""}`}>{r.enabled ? "Active" : "Désactivée"}</span></td>
              <td>{r.event === "run_failed" ? "Une exécution échoue" : `Taux d'échec ≥ ${r.threshold_percent} % (au moins ${r.min_runs} exécutions sur ${r.window_minutes} min)`}<br /><span className="chip">{ENV[r.environment] ?? r.environment}</span></td>
              <td className="mono">{r.destination}</td>
              <td>{r.last_fired_at ? formatDate(r.last_fired_at) : <span className="hint">Jamais</span>}</td>
              <td className="ops-actions">
                <button type="button" onClick={() => test.mutate({ id: r.id })} aria-label={`Tester la règle ${r.name}`}>Tester</button>
                <button type="button" onClick={() => patch.mutate({ id: r.id, enabled: !r.enabled })} aria-label={`${r.enabled ? "Désactiver" : "Activer"} la règle ${r.name}`}>{r.enabled ? "Désactiver" : "Activer"}</button>
                <button type="button" className="danger" onClick={() => setConfirm(r)} aria-label={`Supprimer la règle ${r.name}`}>Supprimer</button>
              </td>
            </tr>))}</tbody></table></div>
      )}
      {creating && (
        <Overlay title="Nouvelle règle d'alerte" description="La règle surveille les exécutions de l'espace et envoie un message à l'adresse indiquée." onClose={() => setCreating(false)}
          footer={<><button type="button" onClick={() => setCreating(false)}>Annuler</button><button type="submit" form={`${fid}-form`} className="primary" disabled={add.isPending}>Créer la règle</button></>}>
          <form id={`${fid}-form`} onSubmit={(e) => { e.preventDefault(); add.mutate(f, { onSuccess: () => setCreating(false) }); }}>
            <RuleForm id={fid} f={f} setF={setF} />
            {add.error && <p role="alert" className="field-error">Erreur : {errorText(add.error)}</p>}
          </form>
        </Overlay>
      )}
      {confirm && (
        <Overlay variant="modal" title={`Supprimer la règle « ${confirm.name} » ?`} description="Vous ne serez plus prévenu par cette règle. L'adresse de destination est effacée." onClose={() => setConfirm(null)}
          footer={<><button type="button" onClick={() => setConfirm(null)}>Annuler</button><button type="button" className="danger" disabled={del.isPending} onClick={() => del.mutate({ id: confirm.id }, { onSuccess: () => setConfirm(null) })}>Supprimer la règle</button></>}>
          <p>Cette action est définitive.</p>
          {del.error && <p role="alert" className="field-error">Erreur : {errorText(del.error)}</p>}
        </Overlay>
      )}
    </>
  );
}

function Retention() {
  const q = useWsQuery<{ retention_days: number | null }>("ops-retention", "/ops/retention/");
  const save = useWsMutation<{ retention_days: number | null }>("PUT", "/ops/retention/", ["ops-retention"]);
  const [days, setDays] = useState<string | null>(null);
  const shown = days ?? (q.data?.retention_days?.toString() ?? "");
  return (
    <Card id="rt" title="Conservation des exécutions" hint="Les exécutions terminées plus anciennes que ce délai sont supprimées automatiquement, avec leurs fichiers. Le journal d'audit n'est jamais supprimé ici. Vide : tout conserver.">
      <div className="field"><label htmlFor="rt-d">Jours de conservation</label><input id="rt-d" type="number" min={1} max={3650} value={shown} placeholder="Tout conserver" onChange={(e) => setDays(e.target.value)} /></div>
      <button type="button" className="primary" onClick={() => save.mutate({ retention_days: shown === "" ? null : Number(shown) }, { onSuccess: () => setDays(null) })} disabled={save.isPending}>Enregistrer</button>
      {save.isSuccess && <span role="status"> ✔ Enregistré.</span>}
      {save.error && <p role="alert" className="field-error">Erreur : {errorText(save.error)}</p>}
    </Card>
  );
}

export function OpsPage() {
  const { can } = useApp();
  const [days, setDays] = useState(7);
  const [env, setEnv] = useState("");
  const [tab, setTab] = useState("overview");
  const q = useWsQuery<Dash>(`ops-dash-${days}-${env}`, `/ops/dashboard/?days=${days}${env ? `&environment=${env}` : ""}`, { refetchInterval: 15000 });
  const d = q.data;
  const overview = (
    <>
      <div className="ops-filters" role="group" aria-label="Filtres">
        <div className="field"><label htmlFor="op-d">Période</label><select id="op-d" value={days} onChange={(e) => setDays(Number(e.target.value))}><option value={1}>24 heures</option><option value={7}>7 jours</option><option value={30}>30 jours</option><option value={90}>90 jours</option></select></div>
        <div className="field"><label htmlFor="op-e">Environnement</label><select id="op-e" value={env} onChange={(e) => setEnv(e.target.value)}><option value="">Tous</option><option value="prod">Production</option><option value="test">Recette</option><option value="dev">Développement</option></select></div>
      </div>
      {q.isLoading && <p role="status">Chargement…</p>}
      {d && <Overview d={d} />}
    </>
  );
  return (
    <>
      <PageHeader title="Exploitation" description="Santé des exécutions de l'espace : volumes, taux d'échec, durées, file d'attente, alertes et conservation." />
      {can("administer") ? (
        <Tabs label="Sections de l'exploitation" active={tab} onChange={setTab} tabs={[
          { id: "overview", label: "Vue d'ensemble", content: overview },
          { id: "alerts", label: "Alertes", content: <Alerts /> },
          { id: "retention", label: "Conservation", content: <Retention /> },
        ]} />
      ) : overview}
    </>
  );
}
