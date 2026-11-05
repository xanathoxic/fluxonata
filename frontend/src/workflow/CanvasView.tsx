import {
  Background, BackgroundVariant, BaseEdge, Controls, EdgeLabelRenderer, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, getSmoothStepPath,
  useReactFlow, applyNodeChanges, type Connection, type Edge, type EdgeProps, type Node, type NodeChange, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Action } from "../api";
import { placeNewNodes, wouldCreateCycle } from "./graphOps";
import { BrandIcon } from "../components/BrandIcon";
import { NodeIcon } from "./icons";
import { DRAG_MIME, type PaletteItem } from "./Palette";
import { edgeText, NODE_LABELS, type Graph, type GraphNode, type Issue } from "./types";

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
        {node.integration ? <BrandIcon slug={node.integration} name={node.integration.charAt(0).toUpperCase() + node.integration.slice(1)} size={36} /> : <NodeIcon kind={node.type} size={32} />}
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
  return <div className="flow-endpoint" role="img" aria-label="Début du workflow"><NodeIcon kind="start" size={44} /><span className="flow-endlabel" aria-hidden="true">Début</span><Handle type="source" position={Position.Right} /></div>;
}
function EndNode() {
  return <div className="flow-endpoint end" role="img" aria-label="Fin du workflow"><NodeIcon kind="end" size={44} /><span className="flow-endlabel" aria-hidden="true">Fin</span><Handle type="target" position={Position.Left} /></div>;
}
function NoteNode({ data, selected }: NodeProps<Node<NoteData>>) {
  return <div className={`flow-note${selected ? " selected" : ""}`}><NodeIcon kind="note" size={16} /> {data.text || "Note vide"}</div>;
}
const nodeTypes = { step: StepNode, start: StartNode, end: EndNode, note: NoteNode };

interface RoutedData extends Record<string, unknown> { cx?: number; editable: boolean; name: string; onBend: (cx: number | null) => void }

/** A line whose vertical segment the person can move (drag the handle, or use the arrow keys; double-click / Échap resets it). */
function RoutedEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, label, style, selected, data }: EdgeProps<Edge<RoutedData>>) {
  const { screenToFlowPosition } = useReactFlow();
  const [drag, setDrag] = useState<number | null>(null);
  const cx = drag ?? data?.cx;
  const [path, lx, ly] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, centerX: cx });
  const x = cx ?? lx;
  const editable = !!data?.editable;
  const step = (dx: number) => data?.onBend(Math.round((cx ?? lx) + dx));
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} label={label} labelX={lx} labelY={ly} />
      {editable && selected && (
        <EdgeLabelRenderer>
          <button
            type="button" className="edge-handle nodrag nopan" style={{ transform: `translate(-50%, -50%) translate(${x}px, ${ly}px)` }}
            aria-label={`Déplacer la ligne vers « ${data?.name ?? ""} » : glisser, ou flèches gauche et droite ; Échap pour la remettre en automatique`}
            onPointerDown={(e) => {
              e.stopPropagation();
              (e.target as HTMLElement).setPointerCapture(e.pointerId);
              setDrag(x);
            }}
            onPointerMove={(e) => { if (drag !== null) setDrag(Math.round(screenToFlowPosition({ x: e.clientX, y: e.clientY }).x)); }}
            onPointerUp={() => { if (drag !== null) data?.onBend(drag); setDrag(null); }}
            onDoubleClick={() => data?.onBend(null)}
            onKeyDown={(e) => {
              if (e.key === "ArrowLeft") { e.preventDefault(); step(-10); }
              else if (e.key === "ArrowRight") { e.preventDefault(); step(10); }
              else if (e.key === "Escape") { e.preventDefault(); data?.onBend(null); }
            }}
          />
        </EdgeLabelRenderer>
      )}
    </>
  );
}
const edgeTypes = { routed: RoutedEdge };

interface Props {
  graph: Graph;
  actions?: Action[];
  issues: Issue[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  /** Centre the view on this node (each new `seq` triggers it again, even for the same node). */
  focus?: { id: string; seq: number } | null;
  onOpen?: (id: string) => void;
  readOnly?: boolean;
  statuses?: Record<string, string>;
  counts?: Record<string, number>;
  onMove?: (moves: { id: string; x: number; y: number }[]) => void;
  onBendEdge?: (from: string, to: string, cx: number | null) => void;
  onConnect?: (from: string, to: string) => void;
  onDeleteNodes?: (ids: string[]) => void;
  onDeleteEdges?: (edges: { from: string; to: string }[]) => void;
  onDropItem?: (item: PaletteItem, pos: { x: number; y: number }) => void;
}

function Inner({ graph, issues, selectedId, onSelect, focus, onOpen, readOnly, statuses, counts, onMove, onBendEdge, onConnect, onDeleteNodes, onDeleteEdges, onDropItem }: Props) {
  const { screenToFlowPosition, fitView, getNode, setCenter, getZoom } = useReactFlow();
  const wrap = useRef<HTMLDivElement>(null);
  const userMoved = useRef(false);
  // « Aller à… »: centre the view on the chosen node. Slightly delayed: opening the properties panel resizes the canvas first.
  useEffect(() => {
    if (!focus) return;
    const t = setTimeout(() => {
      const n = getNode(focus.id);
      if (!n) return;
      void setCenter(n.position.x + (n.measured?.width ?? 120) / 2, n.position.y + (n.measured?.height ?? 60) / 2, { zoom: Math.max(getZoom(), 0.9), duration: 300 });
    }, 120);
    return () => clearTimeout(t);
  }, [focus, getNode, setCenter, getZoom]);
  // A read-only view (an execution) keeps the whole diagram in sight: it is fitted again whenever its box changes size (the page
  // sizes it after the first paint), until the person pans or zooms by hand.
  useEffect(() => {
    const el = wrap.current;
    if (!readOnly || !el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => { if (!userMoved.current) void fitView({ padding: 0.15, maxZoom: 1, minZoom: 0.02 }); });
    ro.observe(el);
    return () => ro.disconnect();
  }, [readOnly, fitView]);
  const [edgeId, setEdgeId] = useState<string | null>(null); // the line whose route is being adjusted
  const { nodes: graphNodes, edges } = useMemo(() => {
    // a draft written by the AI (or imported) may have no positions: lay them out for display, the saved graph is untouched
    const shown = placeNewNodes(graph);
    const ui = shown.nodes.map((n) => n.ui ?? { x: 0, y: 0 });
    const minX = Math.min(0, ...ui.map((p) => p.x));
    const maxX = Math.max(0, ...ui.map((p) => p.x));
    const hasIn = new Set(graph.edges.map((e) => e.to));
    const hasOut = new Set(graph.edges.map((e) => e.from));
    const entries = graph.nodes.filter((n) => !hasIn.has(n.id));
    const sinks = graph.nodes.filter((n) => !hasOut.has(n.id));
    const startY = shown.nodes.find((n) => n.id === entries[0]?.id)?.ui?.y ?? 0;
    const rfNodes: Node[] = [
      { id: "__start__", type: "start", position: graph.layout?.start ?? { x: minX - 120, y: startY + 6 }, draggable: !readOnly, selectable: false, data: {} },
      ...shown.nodes.map((n) => ({
        id: n.id, type: "step", position: n.ui ?? { x: 0, y: 0 }, selected: n.id === selectedId, draggable: !readOnly,
        data: { node: n, errors: issues.filter((i) => i.node === n.id && i.level === "error").length, warnings: issues.filter((i) => i.node === n.id && i.level === "warning").length, runStatus: statuses?.[n.id], runCount: counts?.[n.id] } as StepData,
      })),
      ...(shown.notes ?? []).map((n) => ({ id: n.id, type: "note", position: n.ui ?? { x: 0, y: -140 }, selected: n.id === selectedId, draggable: !readOnly, data: { text: n.text } as NoteData })),
      { id: "__end__", type: "end", position: graph.layout?.end ?? { x: maxX + 220, y: (shown.nodes.find((n) => n.id === sinks[0]?.id)?.ui?.y ?? 0) + 6 }, draggable: !readOnly, selectable: false, data: {} },
    ];
    const mark = { type: MarkerType.ArrowClosed };
    const rfEdges: Edge[] = [
      ...entries.map((n) => ({ id: `s->${n.id}`, source: "__start__", target: n.id, markerEnd: mark, selectable: false, deletable: false })),
      ...graph.edges.map((e) => ({
        id: `${e.from}->${e.to}`, source: e.from, target: e.to, markerEnd: mark, type: "routed", selected: edgeId === `${e.from}->${e.to}`,
        data: { cx: e.ui?.cx, editable: !readOnly, name: graph.nodes.find((n) => n.id === e.to)?.name ?? e.to, onBend: (cx: number | null) => onBendEdge?.(e.from, e.to, cx) } as RoutedData,
        label: e.label ? edgeText(e.label) : undefined, className: e.label === "error" ? "edge-error" : undefined,
        style: e.label === "error" ? { strokeDasharray: "6 4" } : undefined,
      })),
      ...sinks.map((n) => ({ id: `${n.id}->e`, source: n.id, target: "__end__", markerEnd: mark, selectable: false, deletable: false })),
    ];
    return { nodes: rfNodes, edges: rfEdges };
  }, [graph, issues, selectedId, readOnly, statuses, counts, onBendEdge, edgeId]);

  // React Flow works on a local copy of the nodes: positions follow the pointer WHILE dragging; the graph (draft, undo
  // history, autosave) is updated once, when the node is dropped.
  const [nodes, setNodes] = useState<Node[]>(graphNodes);
  const dragging = useRef(false); // a re-render of the page must never snap a node back while it is being dragged
  useEffect(() => { if (!dragging.current) setNodes(graphNodes); }, [graphNodes]);
  const onNodesChange = useCallback((changes: NodeChange[]) => setNodes((ns) => applyNodeChanges(changes, ns)), []);

  return (
    <div ref={wrap} className="flow-drop" onDragOver={(e) => { if (e.dataTransfer.types.includes(DRAG_MIME)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } }}
      onDrop={(e) => {
        const raw = e.dataTransfer.getData(DRAG_MIME);
        if (!raw || readOnly || !onDropItem) return;
        e.preventDefault();
        onDropItem(JSON.parse(raw) as PaletteItem, screenToFlowPosition({ x: e.clientX, y: e.clientY }));
      }}>
      <ReactFlow
        nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onNodesChange={onNodesChange} fitView fitViewOptions={{ padding: 0.15, maxZoom: 1, ...(readOnly ? { minZoom: 0.02 } : {}) }} minZoom={readOnly ? 0.02 : 0.2} maxZoom={2}
        onMoveStart={(e) => { if (e) userMoved.current = true; }}
        nodesConnectable={!readOnly} elementsSelectable zoomOnDoubleClick={false}
        onNodeClick={(_, n) => { setEdgeId(null); if (!n.id.startsWith("__") || readOnly) onSelect(n.id); }} onPaneClick={() => { onSelect(null); setEdgeId(null); }} onEdgeClick={(_, e) => setEdgeId(e.id)}
        onNodeDoubleClick={(_, n) => !n.id.startsWith("__") && onOpen?.(n.id)}
        onNodeDragStart={() => { dragging.current = true; }}
        onNodeDragStop={(_, n, dragged) => {
          onMove?.((dragged.length ? dragged : [n]).map((d) => ({ id: d.id, x: Math.round(d.position.x), y: Math.round(d.position.y) })));
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
