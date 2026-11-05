import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { type StepRun, type WorkflowRun } from "../api";
import { Fields } from "../components/Fields";
import { RunInspectorPanel } from "./RunInspector";
import { useFitHeight } from "./useFitHeight";
import { CanvasView } from "./CanvasView";
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
    inputs: { type: "object", properties: {} }, outputs: {}, edges: (run.graph?.edges ?? []).map((e) => ({ from: e.from, to: e.to, label: e.label as never, ...(e.ui ? { ui: e.ui } : {}) })),
    ...(run.graph?.layout ? { layout: run.graph.layout } : {}),
    notes: run.graph?.notes ?? [],
    nodes: (run.graph?.nodes ?? []).map((n): GraphNode => ({ id: n.id, name: n.name, type: n.type as NodeType, ...(n.ui ? { ui: n.ui } : {}) })),
  };
}

const INSP = { min: 120, def: 300, key: "orch.run.inspector.h" } as const;

/** The height the user chose (remembered), or null: then the inspector takes 40% of the room. */
function loadHeight(): number | null {
  try { const raw = localStorage.getItem(INSP.key); const v = raw === null ? NaN : Number(raw); return Number.isFinite(v) && v >= INSP.min ? v : null; } catch { return null; }
}

/** Horizontal splitter between the diagram (top) and the inspector (bottom): drag it, or focus it and use ↑/↓ (Shift = larger steps);
 *  double-click or Home restores the default height. */
function RowResizer({ value, max, onChange }: { value: number; max: number; onChange: (v: number) => void }) {
  const start = useRef<{ y: number; v: number } | null>(null);
  const clamp = (v: number) => Math.min(max, Math.max(INSP.min, Math.round(v)));
  return (
    <div
      role="separator" aria-orientation="horizontal" aria-label="Redimensionner l'inspecteur de variables" aria-valuenow={value} aria-valuemin={INSP.min} aria-valuemax={max} tabIndex={0}
      className="row-resizer" title="Glisser pour agrandir ou réduire l'inspecteur ; double-clic : hauteur par défaut"
      onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); start.current = { y: e.clientY, v: value }; }}
      onPointerMove={(e) => { if (start.current) onChange(clamp(start.current.v - (e.clientY - start.current.y))); }}
      onPointerUp={(e) => { start.current = null; e.currentTarget.releasePointerCapture(e.pointerId); }}
      onPointerCancel={() => { start.current = null; }}
      onDoubleClick={() => onChange(INSP.def)}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 96 : 24;
        if (e.key === "ArrowUp" || e.key === "ArrowDown") { e.preventDefault(); onChange(clamp(value + (e.key === "ArrowUp" ? step : -step))); }
        else if (e.key === "Home") { e.preventDefault(); onChange(INSP.def); }
      }}
    />
  );
}

/** Diagram of ONE execution (frozen definition) with the state of every step; the variable inspector sits below it.
 *  `fill`: use the whole page (width and height) with a resizable inspector. */
export function RunSchemaView({ run, fill = false }: { run: WorkflowRun; fill?: boolean }) {
  const [sel, setSel] = useState<string | null>(null);
  const [chosen, setChosen] = useState<number | null>(loadHeight);
  const box = useRef<HTMLDivElement>(null);
  const avail = useFitHeight(box, 320, fill) ?? 0;
  const steps = run.steps ?? [];
  const statuses = Object.fromEntries(steps.map((s: StepRun) => [s.node_id, s.status]));
  const change = (v: number) => { setChosen(v); try { localStorage.setItem(INSP.key, String(v)); } catch { /* preference only */ } };
  const max = Math.max(INSP.min, (avail || 600) - 160); // always keep some of the diagram visible
  const height = Math.min(chosen ?? Math.max(INSP.min, Math.round((avail || 600) * 0.4)), max);
  return (
    <div ref={box} className={fill ? "run-graph-tab fill" : "run-graph-tab"} style={fill && avail ? { height: avail } : undefined}>
      <div className="run-schema single"><CanvasView graph={runGraph(run)} issues={[]} selectedId={sel} onSelect={setSel} readOnly statuses={statuses} /></div>
      {fill && <RowResizer value={height} max={max} onChange={change} />}
      <div className="run-inspector-wrap" style={fill ? { height } : undefined}><RunInspectorPanel run={run} selectedId={sel} /></div>
    </div>
  );
}
