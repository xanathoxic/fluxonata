import { Badge, type Tone } from "../components/AdminUi";
import { ReadRegion } from "../components/ReadRegion";
import type { StepRun } from "../api";

/** Le résultat d'une étape Ansible (ADR 0037) : un bilan par machine, une ligne par tâche, les valeurs publiées. */
interface AnsibleTask {
  host: string;
  task: string;
  status: string;
  changed: boolean;
  msg: string;
  ignored?: boolean;
}
interface HostStats {
  ok: number;
  changed: number;
  failures: number;
  unreachable: number;
  skipped: number;
  rescued: number;
  ignored: number;
}
export interface AnsibleResult {
  ok: boolean;
  check?: boolean;
  hosts: number;
  stats: Record<string, HostStats>;
  tasks: AnsibleTask[];
  tasks_dropped?: number;
  data?: Record<string, unknown>;
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** Le résultat Ansible d'une étape, ou `null` : des statistiques par machine et une liste de tâches, rien d'autre ne convient. */
export function ansibleResult(step: StepRun): AnsibleResult | null {
  const o = step.output;
  if (!isObject(o) || !isObject(o.stats) || !Array.isArray(o.tasks) || typeof o.hosts !== "number") return null;
  return o as unknown as AnsibleResult;
}

/** Une étape a un journal Ansible quand elle en a le résultat, ou qu'elle a échoué sur une erreur Ansible (alors le journal dit pourquoi). */
export function hasAnsibleLog(step: StepRun): boolean {
  return ansibleResult(step) !== null || !!step.error?.code?.startsWith("ANSIBLE_");
}

const STATUS: Record<string, { label: string; tone: Tone }> = {
  ok: { label: "OK", tone: "ok" },
  failed: { label: "Échec", tone: "bad" },
  unreachable: { label: "Injoignable", tone: "bad" },
  skipped: { label: "Ignorée", tone: "muted" },
};

function TaskStatus({ t }: { t: AnsibleTask }) {
  const s = STATUS[t.status] ?? { label: t.status, tone: "muted" as Tone };
  const changed = t.status === "ok" && t.changed;
  return (
    <>
      <Badge tone={changed ? "warn" : s.tone}>{changed ? "Modifiée" : s.label}</Badge>
      {t.ignored && <span className="hint"> (erreur ignorée)</span>}
    </>
  );
}

function StepAnsible({ step }: { step: StepRun }) {
  const r = ansibleResult(step);
  const hosts = r ? Object.entries(r.stats) : [];
  const data = r?.data && Object.keys(r.data).length ? r.data : null;
  return (
    <section className="ansible-step" aria-label={`Ansible : ${step.name}`}>
      <h3>
        {step.name} {r && (r.ok ? <Badge tone="ok">Réussi</Badge> : <Badge tone="bad">En échec</Badge>)}{" "}
        {r?.check && <Badge tone="info">Vérification : rien n'a été modifié</Badge>}
      </h3>
      {step.error && (
        <p role="alert" className="field-error">
          {step.error.message}
        </p>
      )}
      {r && (
        <>
          <div className="table-wrap">
            <table className="adm-table">
              <caption className="sr-only">Bilan par machine</caption>
              <thead>
                <tr>
                  <th scope="col">Machine</th>
                  <th scope="col">OK</th>
                  <th scope="col">{r.check ? "Changerait" : "Modifiées"}</th>
                  <th scope="col">Échecs</th>
                  <th scope="col">Injoignable</th>
                  <th scope="col">Ignorées</th>
                </tr>
              </thead>
              <tbody>
                {hosts.map(([name, s]) => (
                  <tr key={name}>
                    <th scope="row">{name}</th>
                    <td>{s.ok}</td>
                    <td>{s.changed}</td>
                    <td>{s.failures ? <Badge tone="bad">{s.failures}</Badge> : 0}</td>
                    <td>{s.unreachable ? <Badge tone="bad">{s.unreachable}</Badge> : 0}</td>
                    <td>{s.skipped}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="table-wrap">
            <table className="adm-table">
              <caption className="sr-only">Tâches jouées</caption>
              <thead>
                <tr>
                  <th scope="col">Machine</th>
                  <th scope="col">Tâche</th>
                  <th scope="col">Résultat</th>
                  <th scope="col">Message</th>
                </tr>
              </thead>
              <tbody>
                {r.tasks.map((t, i) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: une même tâche peut revenir pour une même machine ; l'ordre de jeu est l'identité de la ligne
                  <tr key={i}>
                    <td>{t.host}</td>
                    <td>{t.task}</td>
                    <td>
                      <TaskStatus t={t} />
                    </td>
                    <td>{t.msg || "—"}</td>
                  </tr>
                ))}
                {r.tasks.length === 0 && (
                  <tr>
                    <td colSpan={4} className="hint">
                      Aucune tâche jouée.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {(r.tasks_dropped ?? 0) > 0 && <p className="hint">{r.tasks_dropped} tâche(s) de plus ne sont pas listées (2 000 au plus) : voir le journal.</p>}
          {data && (
            <>
              <h4>Valeurs publiées par le playbook</h4>
              <ReadRegion label={`Valeurs publiées par ${step.name}`}>{JSON.stringify(data, null, 2)}</ReadRegion>
            </>
          )}
        </>
      )}
      <h4>Journal d'Ansible</h4>
      {step.logs ? (
        <ReadRegion label={`Journal d'Ansible de ${step.name}`}>{step.logs}</ReadRegion>
      ) : (
        <p className="hint">Aucun journal enregistré pour cette étape.</p>
      )}
    </section>
  );
}

/** Le contenu de l'onglet « Ansible » d'une exécution : une section par étape Ansible. */
export function AnsibleTab({ steps }: { steps: StepRun[] }) {
  return (
    <>
      {steps.filter(hasAnsibleLog).map((s) => (
        <StepAnsible key={s.node_id} step={s} />
      ))}
    </>
  );
}
