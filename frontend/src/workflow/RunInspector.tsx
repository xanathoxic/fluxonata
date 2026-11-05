import type { ReactNode } from "react";
import type { StepRun, WorkflowRun } from "../api";
import { display } from "../components/Fields";
import { StatusBadge } from "../components/StatusBadge";
import { STATUS_LABEL } from "./CanvasView";

/** A titled Variable / Valeur table (values already masked by the server). */
export function VariableBlock({ title, data, empty }: { title: string; data: Record<string, unknown> | null | undefined; empty: string }): ReactNode {
  return (
    <section className="inspector-block" aria-label={title}>
      <h4>{title}</h4>
      {data && Object.keys(data).length > 0 ? (
        <table className="inspector-table">
          <caption className="sr-only">{title}</caption>
          <thead><tr><th scope="col">Variable</th><th scope="col">Valeur</th></tr></thead>
          <tbody>{Object.entries(data).map(([k, v]) => <tr key={k}><th scope="row"><code>{k}</code></th><td>{display(v)}</td></tr>)}</tbody>
        </table>
      ) : <p className="hint">{empty}</p>}
    </section>
  );
}

/** Variable inspector: for the selected node of the diagram, what it received and what it produced.
 *  Start = workflow inputs + constants; End = workflow outputs. Secrets are masked by the server. */
export function RunInspectorPanel({ run, selectedId }: { run: WorkflowRun; selectedId: string | null }) {
  const insp = run.inspector;
  const step: StepRun | undefined = run.steps?.find((s) => s.node_id === selectedId);
  let title = "Inspecteur de variables";
  let badge: ReactNode = null;
  let body: ReactNode;
  if (!insp) {
    body = <p className="hint">L'inspecteur n'est pas disponible pour cette exécution.</p>;
  } else if (selectedId === "__start__") {
    title = "Début du workflow";
    body = <><VariableBlock title="Entrées" data={insp.start.inputs} empty="Ce workflow n'a pas d'entrée." /><VariableBlock title="Constantes" data={insp.start.constants} empty="Ce workflow n'a pas de constante." /></>;
  } else if (selectedId === "__end__") {
    title = "Fin du workflow";
    body = <VariableBlock title="Sorties du workflow" data={insp.end.outputs} empty={run.status === "succeeded" ? "Ce workflow ne déclare aucune sortie." : "Pas de sortie : l'exécution n'est pas terminée avec succès."} />;
  } else if (selectedId) {
    const node = run.graph?.nodes.find((n) => n.id === selectedId);
    title = node?.name ?? selectedId;
    badge = step ? <><StatusBadge status={step.status} label={STATUS_LABEL[step.status] ?? step.status_label} />{step.attempt > 1 && <span className="hint"> {step.attempt} tentatives</span>}</> : null;
    body = !step ? <p className="hint">Cette étape n'a pas été exécutée : elle n'a ni entrée ni sortie.</p> : (
      <>
        {step.error && <p className="field-error">{step.error.message}{step.error.next_step ? ` — ${step.error.next_step}` : ""}</p>}
        <VariableBlock title="Entrées" data={insp.nodes[selectedId]?.inputs} empty="Cette étape ne reçoit aucune entrée paramétrée." />
        <VariableBlock title="Sorties" data={step.output} empty={step.status === "succeeded" ? "Cette étape ne produit aucune sortie." : "Pas de sortie pour le moment."} />
      </>
    );
  } else {
    body = <p className="hint">Cliquez sur « Début », « Fin » ou une étape du schéma pour voir ses variables d'entrée et de sortie. Les mots de passe et secrets sont masqués.</p>;
  }
  return (
    <section className="run-inspector card" aria-labelledby="insp-title" aria-live="polite">
      <h3 id="insp-title" className="insp-title">{title}{badge}</h3>
      <div className="inspector-grid">{body}</div>
    </section>
  );
}
