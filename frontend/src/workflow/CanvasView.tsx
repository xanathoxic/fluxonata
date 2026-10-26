import {
  Background, BackgroundVariant, Controls, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, useReactFlow,
  applyNodeChanges, type Connection, type Edge, type Node, type NodeChange, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Action } from "../api";
import { wouldCreateCycle } from "./graphOps";
import { NodeIcon } from "./icons";
import { DRAG_MIME, type PaletteItem } from "./Palette";
import { EDGE_LABELS, NODE_LABELS, type EdgeLabel, type Graph, type GraphNode, type Issue } from "./types";

export const STATUS_LABEL: Record<string, string> = {
  succeeded: "Réussie", failed: "Échouée", running: "En cours", waiting: "En attente", retry_wait: "Nouvelle tentative", uncertain: "Issue incertaine",
  skipped: "Ignorée", cancelled: "Annulée",
};

interface StepData extends Record<string, unknown> { node: GraphNode; errors: number; warnings: number; runStatus?: string; runCount?: number }
interface NoteData extends Record<string, unknown> { text: string }

function StepNode({ data, selected }: NodeProps<Node<StepData>>) {
  const { node, errors, warnings, runStatus, runCount } = data;
  const state = runStatus ?? (errors ? "invalid" : "ok");
  return (
    <div className={`flow-node flow-${node.type} state-${state}${selected ? " selected" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <div className="flow-box">
        <NodeIcon kind={node.type} size={32} />
        {errors > 0 && <span className="flow-badge bad" title={`${errors} problème(s)`}><span aria-hidden="true">✖</span><span className="sr-only">{errors} problème(s)</span></span>}
        {errors === 0 && warnings > 0 && <span className="flow-badge warn" title={`${warnings} avertissement(s)`}><span aria-hidden="true">⚠</span><span className="sr-only">{warnings} avertissement(s)</span></span>}
        {runStatus && <span className={`flow-badge run-${runStatus}`}><span aria-hidden="true">{{ succeeded: "✔", failed: "✖", running: "⟳", waiting: "⏸", skipped: "↷", cancelled: "⊘", retry_wait: "⏳", uncertain: "❓" }[runStatus] ?? "•"}</span><span className="sr-only">{STATUS_LABEL[runStatus] ?? runStatus}</span></span>}
      </div>
      {runCount ? <span className="flow-count" title={`${runCount} exécution(s) à cette étape`}><span aria-hidden="true">×{runCount}</span><span className="sr-only">{runCount} exécution(s) à cette étape</span></span> : null}
      <div className="flow-label">{node.name}</div>
      <div className="flow-type">{NODE_LABELS[node.type]}</div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
function StartNode() {
  return <div className="flow-endpoint" role="img" aria-label="Début du workflow"><NodeIcon kind="start" size={44} /><Handle type="source" position={Position.Right} /></div>;
}
function EndNode() {
  return <div className="flow-endpoint end" role="img" aria-label="Fin du workflow"><NodeIcon kind="end" size={44} /><Handle type="target" position={Position.Left} /></div>;
}
function NoteNode({ data, selected }: NodeProps<Node<NoteData>>) {
  return <div className={`flow-note${selected ? " selected" : ""}`}><NodeIcon kind="note" size={16} /> {data.text || "Note vide"}</div>;
}
const nodeTypes = { step: StepNode, start: StartNode, end: EndNode, note: NoteNode };

interface Props {
  graph: Graph;
  actions?: Action[];
  issues: Issue[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onOpen?: (id: string) => void;
  readOnly?: boolean;
  statuses?: Record<string, string>;
  counts?: Record<string, number>;
  onMove?: (moves: { id: string; x: number; y: number }[]) => void;
  onConnect?: (from: string, to: string) => void;
  onDeleteNodes?: (ids: string[]) => void;
  onDeleteEdges?: (edges: { from: string; to: string }[]) => void;
  onDropItem?: (item: PaletteItem, pos: { x: number; y: number }) => void;
}

function Inner({ graph, issues, selectedId, onSelect, onOpen, readOnly, statuses, counts, onMove, onConnect, onDeleteNodes, onDeleteEdges, onDropItem }: Props) {
  const { screenToFlowPosition } = useReactFlow();
  const { nodes: graphNodes, edges } = useMemo(() => {
    const ui = graph.nodes.map((n) => n.ui ?? { x: 0, y: 0 });
    const minX = Math.min(0, ...ui.map((p) => p.x));
    const maxX = Math.max(0, ...ui.map((p) => p.x));
    const hasIn = new Set(graph.edges.map((e) => e.to));
    const hasOut = new Set(graph.edges.map((e) => e.from));
    const entries = graph.nodes.filter((n) => !hasIn.has(n.id));
    const sinks = graph.nodes.filter((n) => !hasOut.has(n.id));
    const startY = entries[0]?.ui?.y ?? 0;
    const rfNodes: Node[] = [
      { id: "__start__", type: "start", position: { x: minX - 120, y: startY + 6 }, draggable: false, selectable: false, data: {} },
      ...graph.nodes.map((n) => ({
        id: n.id, type: "step", position: n.ui ?? { x: 0, y: 0 }, selected: n.id === selectedId, draggable: !readOnly,
        data: { node: n, errors: issues.filter((i) => i.node === n.id && i.level === "error").length, warnings: issues.filter((i) => i.node === n.id && i.level === "warning").length, runStatus: statuses?.[n.id], runCount: counts?.[n.id] } as StepData,
      })),
      ...(graph.notes ?? []).map((n) => ({ id: n.id, type: "note", position: n.ui, selected: n.id === selectedId, draggable: !readOnly, data: { text: n.text } as NoteData })),
      { id: "__end__", type: "end", position: { x: maxX + 220, y: (sinks[0]?.ui?.y ?? 0) + 6 }, draggable: false, selectable: false, data: {} },
    ];
    const mark = { type: MarkerType.ArrowClosed };
    const rfEdges: Edge[] = [
      ...entries.map((n) => ({ id: `s->${n.id}`, source: "__start__", target: n.id, markerEnd: mark, selectable: false, deletable: false })),
      ...graph.edges.map((e) => ({
        id: `${e.from}->${e.to}`, source: e.from, target: e.to, markerEnd: mark, type: "smoothstep",
        label: e.label ? EDGE_LABELS[e.label as EdgeLabel] : undefined, className: e.label === "error" ? "edge-error" : undefined,
        style: e.label === "error" ? { strokeDasharray: "6 4" } : undefined,
      })),
      ...sinks.map((n) => ({ id: `${n.id}->e`, source: n.id, target: "__end__", markerEnd: mark, selectable: false, deletable: false })),
    ];
    return { nodes: rfNodes, edges: rfEdges };
  }, [graph, issues, selectedId, readOnly, statuses, counts]);

  // React Flow works on a local copy of the nodes: positions follow the pointer WHILE dragging; the graph (draft, undo
  // history, autosave) is updated once, when the node is dropped.
  const [nodes, setNodes] = useState<Node[]>(graphNodes);
  const dragging = useRef(false); // a re-render of the page must never snap a node back while it is being dragged
  useEffect(() => { if (!dragging.current) setNodes(graphNodes); }, [graphNodes]);
  const onNodesChange = useCallback((changes: NodeChange[]) => setNodes((ns) => applyNodeChanges(changes, ns)), []);

  return (
    <div className="flow-drop" onDragOver={(e) => { if (e.dataTransfer.types.includes(DRAG_MIME)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } }}
      onDrop={(e) => {
        const raw = e.dataTransfer.getData(DRAG_MIME);
        if (!raw || readOnly || !onDropItem) return;
        e.preventDefault();
        onDropItem(JSON.parse(raw) as PaletteItem, screenToFlowPosition({ x: e.clientX, y: e.clientY }));
      }}>
      <ReactFlow
        nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} fitView fitViewOptions={{ padding: 0.15, maxZoom: 1 }} minZoom={0.2} maxZoom={2}
        nodesConnectable={!readOnly} elementsSelectable zoomOnDoubleClick={false}
        onNodeClick={(_, n) => !n.id.startsWith("__") && onSelect(n.id)} onPaneClick={() => onSelect(null)}
        onNodeDoubleClick={(_, n) => !n.id.startsWith("__") && onOpen?.(n.id)}
        onNodeDragStart={() => { dragging.current = true; }}
        onNodeDragStop={(_, n, dragged) => {
          onMove?.((dragged.length ? dragged : [n]).filter((d) => !d.id.startsWith("__")).map((d) => ({ id: d.id, x: Math.round(d.position.x), y: Math.round(d.position.y) })));
          dragging.current = false;
        }}
        onConnect={(c: Connection) => c.source && c.target && !c.source.startsWith("__") && !c.target.startsWith("__") && !wouldCreateCycle(graph, c.source, c.target) && onConnect?.(c.source, c.target)}
        onNodesDelete={(ns) => onDeleteNodes?.(ns.map((n) => n.id).filter((i) => !i.startsWith("__")))}
        onEdgesDelete={(es) => onDeleteEdges?.(es.filter((e) => !e.source.startsWith("__") && !e.target.startsWith("__")).map((e) => ({ from: e.source, to: e.target })))}
        deleteKeyCode={readOnly ? null : ["Delete", "Backspace"]} nodesFocusable edgesFocusable proOptions={{ hideAttribution: true }}
        aria-label="Schéma du workflow. Une vue en liste équivalente, entièrement utilisable au clavier, est disponible."
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1.2} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

export function CanvasView(props: Props) {
  return <div className="canvas"><ReactFlowProvider><Inner {...props} /></ReactFlowProvider></div>;
}
