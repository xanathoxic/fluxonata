import { useEffect, useState } from "react";
import { HelpTip } from "./HelpTip";

export interface JobEvent { t: number; phase: string; label: string; state: "running" | "done" | "warning" | "error"; detail: string; pct: number }
export interface JobError { code: string; message: string }
export interface DryScenario { label: string; outcome: { status: string; message: string }; executed: number; steps: { id: string; name: string; state: string; detail: string }[] }
export interface DryRunInfo { ran: boolean; reason?: string; scenarios: DryScenario[]; coverage: { executed?: number; total?: number; never?: string[] }; problems: number }
export interface DraftIssue { node?: string; level: "error" | "warning"; code: string; message: string }
export interface DraftResult {
  workflow: string; issues: DraftIssue[]; graph: { nodes: { id: string; name?: string }[] }; notice?: string;
  check?: { dryrun?: DryRunInfo | null }; usage?: { tokens: number; today: number; budget: number };
}
export interface DraftJob { id: string; status: "queued" | "running" | "succeeded" | "failed" | "cancelled"; progress: number; events: JobEvent[]; result: DraftResult | null; error: JobError | null }

const ICON: Record<JobEvent["state"], string> = { running: "⏳", done: "✔", warning: "⚠", error: "✖" };
const STATE_TEXT: Record<JobEvent["state"], string> = { running: "en cours", done: "terminé", warning: "avec avertissement", error: "en erreur" };

/** Live view of a generation: progress bar + the log of the phases the assistant goes through. */
export function GenerationProgress({ job }: { job: DraftJob }) {
  const start = job.events[0]?.t ?? 0;
  const [now, setNow] = useState(() => Date.now() / 1000);
  const running = job.status === "queued" || job.status === "running";
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(id);
  }, [running]);
  const last = job.events[job.events.length - 1];
  const elapsed = Math.max(0, Math.round((running ? now : last?.t ?? now) - start));
  return (
    <div className="gen-progress">
      <p className="gen-current" role="status">
        <strong>{last?.label ?? "En attente…"}</strong>
        {last?.detail && <span className="hint"> — {last.detail}</span>}
      </p>
      <div className="gen-bar">
        <progress max={100} value={job.progress} aria-label="Avancement de la génération" />
        <span aria-hidden="true">{job.progress} %</span>
        <span className="hint">{elapsed} s</span>
      </div>
      <div className="gen-log" role="log" aria-label="Journal de la génération" aria-live="polite"><ol>
        {job.events.map((e, i) => (
          <li key={`${e.phase}-${i}`} className={`gen-${e.state}`}>
            <span aria-hidden="true">{ICON[e.state]}</span>
            <span className="sr-only">{STATE_TEXT[e.state]} : </span>
            <span className="gen-time hint">+{Math.max(0, Math.round(e.t - start))} s</span>{" "}
            <strong>{e.label}</strong>{e.detail && <span className="hint"> — {e.detail}</span>}
          </li>
        ))}
      </ol></div>
    </div>
  );
}

const OUTCOME: Record<string, string> = { succeeded: "terminé sans erreur", failed: "arrêté", stopped: "interrompu", infra: "impossible" };

/** What the dry run did: scenarios, how far each went, the steps no sample reached. */
export function DryRunReport({ dry }: { dry: DryRunInfo }) {
  if (!dry.ran) return <p className="hint">Essai à blanc non effectué : le bac à sable n'était pas disponible{dry.reason ? ` (${dry.reason})` : ""}.</p>;
  const c = dry.coverage;
  return (
    <details>
      <summary>Essai à blanc : {dry.scenarios.length} scénario(s), {c.executed ?? 0}/{c.total ?? 0} étape(s) exercée(s){dry.problems ? `, ${dry.problems} problème(s)` : ", aucun problème"}</summary>
      <p className="help-row"><HelpTip caption="À savoir">Les scripts et les décisions ont été exécutés pour de vrai dans le bac à sable, sur des jeux d'essai ; les actions, appels de workflows, étapes IA, approbations et attentes sont simulés d'après leur contrat. Ce n'est pas une preuve que le workflow est juste.</HelpTip></p>
      <ul>
        {dry.scenarios.map((s) => (
          <li key={s.label}>
            <strong>{s.label}</strong> : {OUTCOME[s.outcome.status] ?? s.outcome.status}{s.outcome.message ? ` (${s.outcome.message})` : ""} — {s.executed} étape(s) exécutée(s)
          </li>
        ))}
      </ul>
      {!!c.never?.length && <p className="hint">Aucun jeu d'essai n'est passé par : {c.never.join(", ")}.</p>}
    </details>
  );
}

export function DraftReport({ result }: { result: DraftResult }) {
  const names = new Map(result.graph.nodes.map((n) => [n.id, n.name || n.id]));
  // a script written by the assistant must be read by a person before publication: that is a task for you, not a defect
  const toRead = result.issues.filter((i: DraftIssue) => i.code === "ai_review_required");
  const rest = result.issues.filter((i: DraftIssue) => i.code !== "ai_review_required");
  const errors = rest.filter((i) => i.level === "error");
  const warnings = rest.filter((i) => i.level !== "error");
  const label = (i: DraftIssue) => (i.node ? names.get(i.node) ?? i.node : "");
  return (
    <div role="status">
      <p><strong>{result.graph.nodes.length} étape(s) proposée(s).</strong>{" "}
        {toRead.length > 0 && <>{toRead.length} script(s) à relire par vous avant publication. </>}
        {errors.length === 0 && warnings.length === 0 && "Aucun problème détecté par la validation."}
        {errors.length > 0 && <>{errors.length} problème(s) à corriger avant de pouvoir publier. </>}
        {warnings.length > 0 && <>{warnings.length} avertissement(s).</>}
      </p>
      {toRead.length > 0 && (
        <section aria-label="Scripts à relire">
          <h3>Scripts à relire avant publication <HelpTip>L'assistant a écrit ces scripts : la règle de sécurité de la plateforme exige qu'une personne les relise. Ouvrez chacun dans l'éditeur, relisez le code, testez-le, puis confirmez « J'ai relu ce code ».</HelpTip></h3>
          <ul>{toRead.map((i, k) => <li key={k}><strong>{label(i)}</strong></li>)}</ul>
        </section>
      )}
      {errors.length > 0 && (
        <section aria-label="Problèmes à corriger">
          <h3>Problèmes à corriger</h3>
          <ul>{errors.map((i, k) => <li key={k}><span className="field-error">✖ Bloquant</span>{i.node && <> · <strong>{label(i)}</strong></>} : {i.message}</li>)}</ul>
        </section>
      )}
      {warnings.length > 0 && (
        <section aria-label="Avertissements">
          <h3>Avertissements</h3>
          <ul>{warnings.map((i, k) => <li key={k}><span className="hint">⚠ Avertissement</span>{i.node && <> · <strong>{label(i)}</strong></>} : {i.message}</li>)}</ul>
        </section>
      )}
      {result.check?.dryrun && <DryRunReport dry={result.check.dryrun} />}
      {result.usage && <p className="hint">Consommation : {result.usage.tokens.toLocaleString("fr-FR")} jetons ({result.usage.today.toLocaleString("fr-FR")} sur {result.usage.budget.toLocaleString("fr-FR")} aujourd'hui).</p>}
      <p className="help-row"><HelpTip caption="À savoir">Ouvrez l'éditeur : chaque étape en erreur est repérée sur le schéma, et « Valider » refait ce contrôle.</HelpTip></p>
    </div>
  );
}
