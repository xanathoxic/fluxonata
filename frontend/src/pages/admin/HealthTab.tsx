import { useWsQuery } from "../../lib";

interface Health { status: string; items: { key: string; label: string; status: "ok" | "warn" | "error"; message: string; hint: string }[]; running_runs: number }
const ICON = { ok: "✔", warn: "⚠", error: "✖" } as const;
const WORD = { ok: "En ordre", warn: "À surveiller", error: "Problème" } as const;

export function HealthTab() {
  const health = useWsQuery<Health>("health", "/system/health/", { refetchInterval: 5000 });
  return (
    <>
      <h2>État du système</h2>
      {health.isLoading && <p role="status">Chargement…</p>}
      {health.data && (
        <>
          <p role="status"><strong>{ICON[health.data.status as "ok"]} {WORD[health.data.status as "ok"]}</strong> — {health.data.running_runs} exécution(s) de workflow en cours.</p>
          <ul className="plain">
            {health.data.items.map((i) => (
              <li key={i.key} className={`health-item health-${i.status}`}>
                <span aria-hidden="true">{ICON[i.status]}</span>
                <div><strong>{i.label}</strong> <span className="sr-only">({WORD[i.status]})</span><br />{i.message}{i.hint && <><br /><span className="hint">{i.hint}</span></>}</div>
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="hint">Le détail des processus (workers, orchestrateur) est réservé aux administrateurs de la plate-forme : ils servent tous les espaces.</p>
    </>
  );
}
