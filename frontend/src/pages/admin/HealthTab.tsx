import { Badge, Section } from "../../components/AdminUi";
import { useWsQuery } from "../../lib";
import { Kpi } from "../OpsCharts";
import { HelpTip } from "../../components/HelpTip";

type St = "ok" | "warn" | "error";
interface Health { status: string; items: { key: string; label: string; status: St; message: string; hint: string }[]; running_runs: number }
const ICON = { ok: "✔", warn: "⚠", error: "✖" } as const;
const WORD = { ok: "En ordre", warn: "À surveiller", error: "Problème" } as const;
const TONE = { ok: "ok", warn: "warn", error: "bad" } as const;

export function HealthTab() {
  const health = useWsQuery<Health>("health", "/system/health/", { refetchInterval: 5000 });
  const d = health.data;
  const count = (s: St) => d?.items.filter((i) => i.status === s).length ?? 0;
  return (
    <>
      {health.isLoading && <p role="status">Chargement…</p>}
      {d && (
        <>
          <ul className="kpis" aria-label="Résumé de l'état du système">
            <Kpi label="État général" value={`${ICON[d.status as St]} ${WORD[d.status as St]}`} tone={d.status === "ok" ? "ok" : d.status === "error" ? "bad" : undefined} />
            <Kpi label="Contrôles en ordre" value={`${count("ok")} / ${d.items.length}`} tone="ok" />
            <Kpi label="À surveiller" value={count("warn")} hint="avertissements" />
            <Kpi label="En panne" value={count("error")} tone={count("error") > 0 ? "bad" : undefined} />
            <Kpi label="Exécutions en cours" value={d.running_runs} tone="info" />
          </ul>
          <Section title="État du système" hint="Contrôles automatiques, actualisés toutes les 5 secondes.">
            <p role="status" className="sr-only"><strong>{ICON[d.status as St]} {WORD[d.status as St]}</strong> — {d.running_runs} exécution(s) de workflow en cours.</p>
            <ul className="plain adm-health">
              {d.items.map((i) => (
                <li key={i.key} className={`health-item health-${i.status}`}>
                  <span className="adm-health-ico" aria-hidden="true">{ICON[i.status]}</span>
                  <div>
                    <strong>{i.label}</strong> <Badge tone={TONE[i.status]}>{WORD[i.status]}</Badge><br />{i.message}{i.hint && <><br /><span className="hint">{i.hint}</span></>}
                  </div>
                </li>
              ))}
            </ul>
          </Section>
        </>
      )}
      <p className="help-row"><HelpTip caption="À savoir">Le détail des processus (workers, orchestrateur) est réservé aux administrateurs de la plate-forme : ils servent tous les espaces.</HelpTip></p>
    </>
  );
}
