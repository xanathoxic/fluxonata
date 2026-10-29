import { useState } from "react";
import { Link } from "react-router-dom";
import { type StepRun, type WorkflowRun } from "../api";
import { Fields } from "../components/Fields";
import { StatusBadge } from "../components/StatusBadge";
import { CanvasView, STATUS_LABEL } from "./CanvasView";
import type { Graph, GraphNode, NodeType } from "./types";

export function RunSummary({ run }: { run: WorkflowRun }) {
  const steps = run.steps ?? [];
  const ok = steps.filter((s) => s.status === "succeeded" && s.node_type !== "compensation");
  const failed = steps.find((s) => s.status === "failed" && s.node_type !== "compensation");
  const pending = steps.filter((s) => ["running", "waiting", "retry_wait", "uncertain"].includes(s.status));
  return (
    <section aria-labelledby="sum" className={run.status === "failed" ? "error-box" : undefined}>
      <h2 id="sum">En résumé</h2>
      <ul>
        <li>{ok.length} étape(s) réussie(s){ok.length ? ` : ${ok.map((s) => s.name).join(", ")}` : ""}.</li>
        {failed && <li><strong>Échec :</strong> {failed.name} — {failed.error?.message}</li>}
        {pending.length > 0 && <li>En cours ou en attente : {pending.map((s) => s.name).join(", ")}.</li>}
        {steps.filter((s) => s.approval_id && s.status === "waiting").map((s) => <li key={s.node_id}><Link to="/approvals">Une approbation est attendue</Link> pour « {s.name} ».</li>)}
        {run.error?.compensation?.length ? <li>Compensations : {run.error.compensation.map((c) => `${c.step} (${c.status === "succeeded" ? "réussie" : "échouée"})`).join(", ")}.</li> : null}
      </ul>
      {run.status === "succeeded" && run.outputs && Object.keys(run.outputs).length > 0 && (
        <><h3>Résultat</h3><Fields data={run.outputs} /></>
      )}
      {run.status === "failed" && run.error?.next_step && <p><strong>Que faire ensuite :</strong> {run.error.next_step}</p>}
      {run.status === "cancelled" && <p>{run.error?.message}</p>}
    </section>
  );
}


export function runGraph(run: Pick<WorkflowRun, "graph">): Graph {
  return {
    inputs: { type: "object", properties: {} }, outputs: {}, edges: (run.graph?.edges ?? []).map((e) => ({ from: e.from, to: e.to, label: e.label as never })),
    notes: run.graph?.notes ?? [],
    nodes: (run.graph?.nodes ?? []).map((n, i): GraphNode => ({ id: n.id, name: n.name, type: n.type as NodeType, ui: n.ui ?? { x: i * 200, y: 0 } })),
  };
}

/** Diagram of ONE execution (frozen definition) with the state of every step, plus a panel for the selected step. */
export function RunSchemaView({ run }: { run: WorkflowRun }) {
  const [sel, setSel] = useState<string | null>(null);
  const steps = run.steps ?? [];
  const statuses = Object.fromEntries(steps.map((s: StepRun) => [s.node_id, s.status]));
  const step = steps.find((s) => s.node_id === sel) ?? null;
  return (
    <div className="run-schema">
      <CanvasView graph={runGraph(run)} issues={[]} selectedId={sel} onSelect={setSel} readOnly statuses={statuses} />
      <aside aria-live="polite" className="run-step-panel">
        {step ? (
          <>
            <h3>{step.name}</h3>
            <p><StatusBadge status={step.status} label={STATUS_LABEL[step.status] ?? step.status_label} /></p>
            {step.error && <p className="field-error">{step.error.message}{step.error.next_step ? ` — ${step.error.next_step}` : ""}</p>}
            {step.output && step.status === "succeeded" && <Fields data={step.output} />}
            {step.logs && <pre tabIndex={0} aria-label={`Journal de ${step.name}`}>{step.logs}</pre>}
          </>
        ) : <p className="hint">Cliquez sur une étape du schéma pour voir son état, ses résultats et son journal. Couleur ET icône indiquent l'état.</p>}
      </aside>
    </div>
  );
}
