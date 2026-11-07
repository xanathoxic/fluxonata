import {
  Background, BackgroundVariant, BaseEdge, Controls, EdgeLabelRenderer, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, getSmoothStepPath,
  useReactFlow, applyNodeChanges, type Connection, type Edge, type EdgeProps, type Node, type NodeChange, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Action } from "../api";
import { placeNewNodes, wouldCreateCycle } from "./graphOps";
import { labelPoint, MAX_POINTS, roundedPath, segmentMidpoints, type Pt } from "./edgeRoute";
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

interface RoutedData extends Record<string, unknown> { cx?: number; points?: Pt[]; editable: boolean; name: string; onRoute: (points: Pt[] | null) => void }
/** Après une action au clavier, le focus doit rester sur la poignée : le schéma se remonte quelques fois juste après un changement
 *  (re-mesure des étapes), ce qui recrée les poignées. On redonne donc le focus pendant un court instant, jusqu'à un clic ailleurs ou Tab. */
let pendingFocus: { id: string; until: number } | null = null;
function keepFocus(id: string) {
  pendingFocus = { id, until: Date.now() + 2500 };
  document.addEventListener("pointerdown", () => { pendingFocus = null; }, { once: true, capture: true });
}

interface Drag { index: number; pt: Pt; from: Pt; inserted: boolean; moved: boolean }

/** Une poignée du tracé : se glisse n'importe où, se manie aussi au clavier (voir les libellés). */
function RouteHandle({ id, kind, pos, label, onPointerDown, onKeyDown, onDoubleClick }: {
  id: string; kind: "point" | "add"; pos: Pt; label: string;
  onPointerDown: (e: React.PointerEvent<HTMLButtonElement>) => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLButtonElement>) => void; onDoubleClick?: () => void;
}) {
  return (
    <button type="button" id={id} className={`edge-handle edge-${kind} nodrag nopan`} style={{ transform: `translate(-50%, -50%) translate(${pos.x}px, ${pos.y}px)` }}
      aria-label={label} onPointerDown={onPointerDown} onKeyDown={onKeyDown} onDoubleClick={onDoubleClick} />
  );
}

/** Un lien dont la personne place le tracé comme elle veut : des points de passage qu'on glisse (ou déplace aux flèches), un « + » au milieu
 *  de chaque segment pour en ajouter un ; Suppr retire le point, Échap remet le tracé automatique. */
function RoutedEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, label, style, selected, data }: EdgeProps<Edge<RoutedData>>) {
  const { screenToFlowPosition } = useReactFlow();
  const [drag, setDrag] = useState<Drag | null>(null);
  const saved = useMemo(() => data?.points ?? [], [data?.points]);
  const points = useMemo(() => {
    if (!drag) return saved;
    const next = [...saved];
    if (drag.inserted) next.splice(drag.index, 0, drag.pt); else next[drag.index] = drag.pt;
    return next;
  }, [saved, drag]);
  useEffect(() => { // see keepFocus: runs at every render, the handles may have been recreated since the last keyboard action
    if (!pendingFocus) return;
    if (Date.now() > pendingFocus.until) { pendingFocus = null; return; }
    const el = document.getElementById(pendingFocus.id);
    if (el && document.activeElement !== el) el.focus();
  });
  const source = { x: sourceX, y: sourceY };
  const target = { x: targetX, y: targetY };
  const custom = points.length > 0;
  const chain = [source, ...points, target];
  const [smooth, sx, sy] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, centerX: data?.cx });
  const mid = labelPoint(chain);
  const [path, lx, ly] = custom ? [roundedPath(chain), mid.x, mid.y] : [smooth, sx, sy];
  const adds = custom ? segmentMidpoints(chain) : [{ x: data?.cx ?? sx, y: sy }];
  const editable = !!data?.editable;
  const name = data?.name ?? "";
  const route = (next: Pt[] | null) => data?.onRoute(next && next.length > 0 ? next : null);
  // The drag is followed on the window, not on the handle: the handle may be taken off the page while it is dragged (the other « + » are hidden,
  // the diagram can be rebuilt), and it would lose the pointer and never see it released.
  const dragRef = useRef<Drag | null>(null);
  const savedRef = useRef(saved);
  savedRef.current = saved;
  const routeRef = useRef(route);
  routeRef.current = route;
  const grab = (start: Omit<Drag, "from" | "moved">) => (e: React.PointerEvent<HTMLButtonElement>) => {
    e.preventDefault();
    e.stopPropagation();
    const first: Drag = { ...start, from: start.pt, moved: false };
    dragRef.current = first;
    setDrag(first);
    const onMove = (ev: PointerEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const p = screenToFlowPosition({ x: ev.clientX, y: ev.clientY });
      const pt = { x: Math.round(p.x), y: Math.round(p.y) };
      const next = { ...d, pt, moved: d.moved || Math.hypot(pt.x - d.from.x, pt.y - d.from.y) > 3 };
      dragRef.current = next;
      setDrag(next);
    };
    const onEnd = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onEnd);
      window.removeEventListener("pointercancel", onEnd);
      const d = dragRef.current;
      dragRef.current = null;
      if (d?.moved) {
        const next = [...savedRef.current];
        if (d.inserted) next.splice(d.index, 0, d.pt); else next[d.index] = d.pt;
        routeRef.current(next);
      }
      setDrag(null);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onEnd);
    window.addEventListener("pointercancel", onEnd);
  };
  const full = points.length >= MAX_POINTS;
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} label={label} labelX={lx} labelY={ly} />
      {editable && selected && (
        <EdgeLabelRenderer>
          {points.map((p, i) => (
            <RouteHandle key={`p${i}`} id={`${id}-pt-${i}`} kind="point" pos={p}
              label={`Point de passage ${i + 1} sur ${points.length} de la ligne vers « ${name} » : glisser, ou flèches pour le déplacer (Maj : 1 pixel) ; Suppr pour le retirer ; Échap pour remettre la ligne en automatique`}
              onPointerDown={grab({ index: i, pt: p, inserted: false })}
              onDoubleClick={() => { route(saved.filter((_, k) => k !== i)); }}
              onKeyDown={(e) => {
                const step = e.shiftKey ? 1 : 10;
                const delta = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
                if (delta) { e.preventDefault(); keepFocus(`${id}-pt-${i}`); route(saved.map((q, k) => (k === i ? { x: q.x + (delta[0] ?? 0), y: q.y + (delta[1] ?? 0) } : q))); }
                else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); e.stopPropagation(); if (saved.length > 1) keepFocus(`${id}-pt-${Math.max(0, i - 1)}`); route(saved.filter((_, k) => k !== i)); }
                else if (e.key === "Tab") pendingFocus = null;
                else if (e.key === "Escape") { e.preventDefault(); route(null); }
              }} />
          ))}
          {!full && !drag && adds.map((m, k) => (
            <RouteHandle key={`a${k}`} id={`${id}-add-${k}`} kind="add" pos={m}
              label={`Ajouter un point de passage à la ligne vers « ${name} »${adds.length > 1 ? `, segment ${k + 1} sur ${adds.length}` : ""} : glisser, ou Entrée pour en poser un au milieu`}
              onPointerDown={grab({ index: k, pt: { x: Math.round(m.x), y: Math.round(m.y) }, inserted: true })}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); keepFocus(`${id}-pt-${k}`); const next = [...saved]; next.splice(k, 0, { x: Math.round(m.x), y: Math.round(m.y) }); route(next); }
                else if (e.key === "Escape") { e.preventDefault(); route(null); }
              }} />
          ))}
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
  onRouteEdge?: (from: string, to: string, points: Pt[] | null) => void;
  onConnect?: (from: string, to: string) => void;
  onDeleteNodes?: (ids: string[]) => void;
  onDeleteEdges?: (edges: { from: string; to: string }[]) => void;
  onDropItem?: (item: PaletteItem, pos: { x: number; y: number }) => void;
}

function Inner({ graph, issues, selectedId, onSelect, focus, onOpen, readOnly, statuses, counts, onMove, onRouteEdge, onConnect, onDeleteNodes, onDeleteEdges, onDropItem }: Props) {
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
        data: { cx: e.ui?.cx, points: e.ui?.points, editable: !readOnly, name: graph.nodes.find((n) => n.id === e.to)?.name ?? e.to, onRoute: (points: Pt[] | null) => onRouteEdge?.(e.from, e.to, points) } as RoutedData,
        label: e.label ? edgeText(e.label) : undefined, className: e.label === "error" ? "edge-error" : undefined,
        style: e.label === "error" ? { strokeDasharray: "6 4" } : undefined,
      })),
      ...sinks.map((n) => ({ id: `${n.id}->e`, source: n.id, target: "__end__", markerEnd: mark, selectable: false, deletable: false })),
    ];
    return { nodes: rfNodes, edges: rfEdges };
  }, [graph, issues, selectedId, readOnly, statuses, counts, onRouteEdge, edgeId]);

  // React Flow works on a local copy of the nodes: positions follow the pointer WHILE dragging; the graph (draft, undo
  // history, autosave) is updated once, when the node is dropped.
  const [nodes, setNodes] = useState<Node[]>(graphNodes);
  const dragging = useRef(false); // a re-render of the page must never snap a node back while it is being dragged
  // The nodes are rebuilt at every render of the page (autosave, validation…): keep the sizes already measured, otherwise React Flow forgets where
  // the connection points are, takes the lines off and puts them back after measuring again (which also recreates their handles).
  useEffect(() => {
    if (dragging.current) return;
    setNodes((prev) => graphNodes.map((n) => { const old = prev.find((p) => p.id === n.id); return old?.measured ? { ...n, measured: old.measured } : n; }));
  }, [graphNodes]);
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
