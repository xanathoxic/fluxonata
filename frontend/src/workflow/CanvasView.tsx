import {
  Background, BackgroundVariant, BaseEdge, ConnectionLineType, ControlButton, Controls, EdgeLabelRenderer, Handle, MarkerType, NodeResizer, Position, ReactFlow, ReactFlowProvider,
  useReactFlow, applyNodeChanges, type Connection, type Edge, type EdgeProps, type Node, type NodeChange, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Action } from "../api";
import { canLinkEnd, canLinkStart, END_ID, placeNewNodes, START_ID, wouldCreateCycle } from "./graphOps";
import { applyDrag, autoPorts, dragValue, facePoint, GRID, junctionCoords, junctionFaces, trunkPoint, viaRoute, labelPoint, orthogonalRoute, portOffset, roundedPath, simplifyRoute, type Coords, type Face, type Ports, type Pt, type Rect, type Segment, type Via } from "./edgeRoute";
import { px, scaleFactor } from "../scale";
import { NOTE_MIN_H, NOTE_MIN_W, noteSide, noteStyle } from "./noteStyle";
import { isJunctionId } from "./graphOps";
import { BrandIcon } from "../components/BrandIcon";
import { NodeIcon } from "./icons";
import { DRAG_MIME, type PaletteItem } from "./Palette";
import { edgeText, NODE_LABELS, type Graph, type GraphNode, type Issue, type Junction } from "./types";

export const STATUS_LABEL: Record<string, string> = {
  succeeded: "Réussie", failed: "Échouée", running: "En cours", waiting: "En attente", retry_wait: "Nouvelle tentative", uncertain: "Issue incertaine",
  skipped: "Ignorée", cancelled: "Annulée",
};

interface StepData extends Record<string, unknown> { node: GraphNode; errors: number; warnings: number; runStatus?: string; runCount?: number }
interface NoteRect { x: number; y: number; w: number; h: number }
interface NoteData extends Record<string, unknown> { text: string; sized: boolean; color?: string; opacity?: number; editable: boolean; onResize: (id: string, r: NoteRect) => void }

/** Les points de connexion d'une étape : deux par face, un d'entrée et un de sortie, légèrement écartés pour qu'un lien qui arrive et un lien qui part par la
 *  même face ne se confondent pas. Ils sont posés sur l'icône, pas sur le bloc entier avec ses libellés. Identifiant : `i:l` (entrée, face gauche), `o:r`… */
const FACES: [Face, Position][] = [["t", Position.Top], ["r", Position.Right], ["b", Position.Bottom], ["l", Position.Left]];
function Faces() {
  return (
    <>
      {FACES.flatMap(([face, position]) =>
        (["in", "out"] as const).map((role) => {
          const sign = portOffset(face, role) > 0 ? "+" : "-"; // la même répartition que `facePoint`
          const style = face === "l" || face === "r" ? { top: `calc(50% ${sign} ${px(PORT_GAP_PX)})` } : { left: `calc(50% ${sign} ${px(PORT_GAP_PX)})` };
          return <Handle key={`${role}${face}`} id={`${role === "in" ? "i" : "o"}:${face}`} type={role === "in" ? "target" : "source"} position={position} style={style} />;
        }),
      )}
    </>
  );
}
/** L'écart de `facePoint`, en pixels d'interface (avant la mise à l'échelle de l'interface). */
const PORT_GAP_PX = 7;
const FACE_OF: Record<string, Face> = { top: "t", right: "r", bottom: "b", left: "l" };
/** La face d'un identifiant de point de connexion (`i:l` → `l`). */
const faceOfHandle = (id: string | null | undefined): Face | undefined => (["t", "r", "b", "l"] as const).find((f) => id?.slice(2) === f);

function StepNode({ data, selected }: NodeProps<Node<StepData>>) {
  const { node, errors, warnings, runStatus, runCount } = data;
  const state = runStatus ?? (errors ? "invalid" : "ok");
  return (
    <div className={`flow-node flow-${node.type} state-${state}${selected ? " selected" : ""}`}>
      <div className="flow-box">
        <Faces />
        {node.integration ? <BrandIcon slug={node.integration} name={node.integration.charAt(0).toUpperCase() + node.integration.slice(1)} size={36} /> : <NodeIcon kind={node.type} size={32} />}
        {errors > 0 && <span className="flow-badge bad" title={`${errors} problème(s)`}><span aria-hidden="true">✖</span><span className="sr-only">{errors} problème(s)</span></span>}
        {errors === 0 && warnings > 0 && <span className="flow-badge warn" title={`${warnings} avertissement(s)`}><span aria-hidden="true">⚠</span><span className="sr-only">{warnings} avertissement(s)</span></span>}
        {runStatus && <span className={`flow-badge run-${runStatus}`}><span aria-hidden="true">{{ succeeded: "✔", failed: "✖", running: "⟳", waiting: "⏸", skipped: "↷", cancelled: "⊘", retry_wait: "⏳", uncertain: "❓" }[runStatus] ?? "•"}</span><span className="sr-only">{STATUS_LABEL[runStatus] ?? runStatus}</span></span>}
      </div>
      {runCount ? <span className="flow-count" title={`${runCount} exécution(s) à cette étape`}><span aria-hidden="true">×{runCount}</span><span className="sr-only">{runCount} exécution(s) à cette étape</span></span> : null}
      <div className="flow-label">{node.name}</div>
      <div className="flow-type">{NODE_LABELS[node.type]}</div>
    </div>
  );
}
function StartNode() {
  return <div className="flow-endpoint" role="img" aria-label="Début du workflow"><NodeIcon kind="start" size={44} /><span className="flow-endlabel" aria-hidden="true">Début</span><Faces /></div>;
}
function EndNode() {
  return <div className="flow-endpoint end" role="img" aria-label="Fin du workflow"><NodeIcon kind="end" size={44} /><span className="flow-endlabel" aria-hidden="true">Fin</span><Faces /></div>;
}
/** Une note : toujours derrière les étapes et les lignes (voir `.react-flow__node-note`), à la couleur et à l'opacité choisies, redimensionnable à la souris
 *  par ses poignées quand elle est sélectionnée (la largeur et la hauteur se règlent aussi dans ses propriétés). */
function NoteNode({ id, data, selected }: NodeProps<Node<NoteData>>) {
  const st = noteStyle(data.color, data.opacity);
  return (
    <div className={`flow-note${selected ? " selected" : ""}${data.sized ? " sized" : ""}`} style={{ background: st.background, borderColor: st.border, color: st.text }}>
      <NodeResizer isVisible={!!selected && data.editable} minWidth={NOTE_MIN_W} minHeight={NOTE_MIN_H} onResizeEnd={(_e, p) => data.onResize(id, { x: p.x, y: p.y, w: p.width, h: p.height })} />
      <NodeIcon kind="note" size={16} /> {data.text || "Note vide"}
    </div>
  );
}
/** Un point de jonction : un petit rond qui se déplace comme une étape. Son entrée est verte (à gauche), ses sorties rouges (droite, haut, bas) ; plusieurs liens peuvent
 *  partir de la même sortie. Selon son rôle, il est le point commun des liens qui partent d'une étape (on y relie de nouvelles sorties) ou de ceux qui arrivent à une étape
 *  (on y relie de nouvelles entrées). Les liens qui le traversent le suivent. */
const JUNCTION_HANDLE: Record<Face, Position> = { r: Position.Right, l: Position.Left, t: Position.Top, b: Position.Bottom };
interface JunctionData extends Record<string, unknown> { role: "in" | "out"; auto: boolean; flip: boolean }
function JunctionNode({ data, selected }: NodeProps<Node<JunctionData>>) {
  const out = data.role === "out";
  const faces = junctionFaces(data.flip);
  return (
    <div className={`flow-junction${selected ? " selected" : ""}${data.auto ? " auto" : ""}`} role="img" aria-label={`${out ? "Point de jonction : les liens partent d'ici" : "Point de jonction : les liens arrivent ici"}${data.flip ? " (inversé : entrées à gauche, en haut et en bas, sortie à droite)" : ""}`} title="Point de jonction : glissez-le pour déplacer tous ses liens ; double-clic (ou Entrée) pour l'inverser">
      {faces.ins.map((f) => <Handle key={`i${f}`} id={`i:${f}`} type="target" position={JUNCTION_HANDLE[f]} className="jh jin" isConnectable={!out} isConnectableStart={false} />)}
      {faces.outs.map((f) => <Handle key={`o${f}`} id={`o:${f}`} type="source" position={JUNCTION_HANDLE[f]} className="jh jout" isConnectable={out} isConnectableEnd={false} />)}
    </div>
  );
}
const nodeTypes = { junction: JunctionNode, step: StepNode, start: StartNode, end: EndNode, note: NoteNode };

interface RoutedData extends Record<string, unknown> { route?: Coords; pinned?: Ports; boxes: Rect[]; snap?: boolean; hint?: Coords; via?: Via[]; viaR?: number; sharpOut?: boolean; sharpIn?: boolean; editable: boolean; name: string; onRoute: (route: Coords | null) => void }
/** Après une action au clavier, le focus doit rester sur la poignée : le schéma se remonte quelques fois juste après un changement
 *  (re-mesure des étapes), ce qui recrée les poignées. On redonne donc le focus pendant un court instant, jusqu'à un clic ailleurs ou Tab. */
let pendingFocus: { id: string; until: number } | null = null;
function keepFocus(id: string) {
  pendingFocus = { id, until: Date.now() + 2500 };
  document.addEventListener("pointerdown", () => { pendingFocus = null; }, { once: true, capture: true });
}

/** Le segment en cours de glissement : son modèle de départ (celui du tracé enregistré) et la valeur visée (abscisse ou ordonnée du schéma). */
interface Drag { segment: Segment; value: number; from: number; moved: boolean }

const KEYS: Record<"v" | "h", Record<string, number>> = { v: { ArrowLeft: -1, ArrowRight: 1 }, h: { ArrowUp: -1, ArrowDown: 1 } };

/** La poignée d'un segment : une barre posée au milieu du segment, qui se glisse (souris) ou se déplace aux flèches (clavier). */
function SegmentHandle({ id, segment, name, onPointerDown, onKeyDown }: {
  id: string; segment: Segment; name: string;
  onPointerDown: (e: React.PointerEvent<Element>) => void; onKeyDown: (e: React.KeyboardEvent<HTMLButtonElement>) => void;
}) {
  const vertical = segment.orientation === "v";
  return (
    <button type="button" id={id} className={`edge-handle edge-seg edge-seg-${segment.orientation} nodrag nopan`} onPointerDown={onPointerDown} onKeyDown={onKeyDown}
      style={{ transform: `translate(-50%, -50%) translate(${segment.mid.x}px, ${segment.mid.y}px)` }}
      aria-label={`Déplacer le segment ${vertical ? "vertical" : "horizontal"} ${segment.index + 1} de la ligne vers « ${name} » : glisser, ou flèches ${vertical ? "gauche/droite" : "haut/bas"} (Maj : 1 pixel) ; Échap pour remettre la ligne en automatique`} />
  );
}

/** Un lien toujours orthogonal : il sort à droite de l'étape source et entre à gauche de la cible. Chaque segment se déplace : les verticaux
 *  vers la gauche ou la droite, les horizontaux vers le haut ou le bas (un segment attaché à une étape gagne alors deux coudes). On glisse le
 *  segment lui-même (le curseur change) ou sa poignée, visible quand la ligne est sélectionnée ; Échap remet le tracé automatique. */
function RoutedEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, label, style, selected, data }: EdgeProps<Edge<RoutedData>>) {
  const { screenToFlowPosition } = useReactFlow();
  const [drag, setDrag] = useState<Drag | null>(null);
  const source = useMemo(() => ({ x: sourceX, y: sourceY }), [sourceX, sourceY]);
  const target = useMemo(() => ({ x: targetX, y: targetY }), [targetX, targetY]);
  const saved = data?.route ?? data?.hint; // le tracé réglé par la personne, sinon le tronc commun des liens qui partagent un point de connexion, sinon l'automatique
  const ports: Ports = useMemo(() => ({ sf: FACE_OF[sourcePosition] ?? "r", tf: FACE_OF[targetPosition] ?? "l" }), [sourcePosition, targetPosition]);
  const boxes = data?.boxes ?? [];
  const coords = drag ? applyDrag(source, target, ports, saved, drag.segment, drag.value, boxes) : saved;
  const routed = orthogonalRoute(source, target, ports, coords, boxes);
  // un lien qui passe par un point de jonction posé ou déplacé : son tracé suit le point (c'est le point qu'on déplace, pas les segments)
  const viaPoints = data?.via?.length ? viaRoute(source, ports.sf, target, ports.tf, data.via, data.viaR ?? 8, boxes) : null;
  const points = viaPoints ?? routed.points;
  const segments = viaPoints ? [] : routed.segments;
  const mid = labelPoint(points);
  // au tronc commun d'un point de jonction automatique, les branches partent droit, en croix (angle franc sous le rond)
  const sharp = [data?.sharpOut && points[1], data?.sharpIn && points[points.length - 2]].filter((p): p is Pt => !!p && points.length > 3);
  useEffect(() => { // see keepFocus: runs at every render, the handles may have been recreated since the last keyboard action
    if (!pendingFocus) return;
    if (Date.now() > pendingFocus.until) { pendingFocus = null; return; }
    const el = document.getElementById(pendingFocus.id);
    if (el && document.activeElement !== el) el.focus();
  });
  const editable = !!data?.editable;
  const name = data?.name ?? "";
  const setRoute = (next: Coords | null) => data?.onRoute(next);
  // The drag is followed on the window, not on the handle: the diagram can be rebuilt while it is dragged (handles recreated), and a handle that
  // lost the pointer would never see it released.
  const dragRef = useRef<Drag | null>(null);
  const snap = !!data?.snap;
  const live = useRef({ source, target, ports, boxes, saved, setRoute, snap });
  live.current = { source, target, ports, boxes, saved, setRoute, snap };
  const grab = (segment: Segment) => (e: React.PointerEvent<Element>) => {
    if (!editable) return;
    e.preventDefault();
    const origin = segment.orientation === "v" ? segment.mid.x : segment.mid.y;
    dragRef.current = { segment, value: origin, from: origin, moved: false };
    const onMove = (ev: PointerEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const value = dragValue(d.segment, screenToFlowPosition({ x: ev.clientX, y: ev.clientY }), live.current.snap ? GRID : undefined);
      const next = { ...d, value, moved: d.moved || Math.abs(value - d.from) > 3 };
      dragRef.current = next;
      if (next.moved) setDrag(next);
    };
    const onEnd = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onEnd);
      window.removeEventListener("pointercancel", onEnd);
      const d = dragRef.current;
      dragRef.current = null;
      if (d?.moved) {
        const { source: s, target: t, ports: p, boxes: b, saved: sv, setRoute: save } = live.current;
        save(simplifyRoute(s, t, p, applyDrag(s, t, p, sv, d.segment, d.value, b), b) ?? null);
      }
      setDrag(null);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onEnd);
    window.addEventListener("pointercancel", onEnd);
  };
  const keyboard = (segment: Segment) => (e: React.KeyboardEvent<HTMLButtonElement>) => {
    const dir = KEYS[segment.orientation][e.key];
    if (dir) {
      e.preventDefault();
      const wanted = (segment.orientation === "v" ? segment.mid.x : segment.mid.y) + dir * (e.shiftKey ? 1 : snap ? GRID : 8);
      const next = simplifyRoute(source, target, ports, applyDrag(source, target, ports, saved, segment, wanted, boxes), boxes) ?? null;
      // un segment attaché à une étape change de rang quand il gagne des coudes : le focus suit le segment déplacé
      const after = orthogonalRoute(source, target, ports, next ?? undefined, boxes).segments.filter((g) => g.orientation === segment.orientation);
      const value = (g: Segment) => (g.orientation === "v" ? g.mid.x : g.mid.y);
      const moved = after.reduce<Segment | undefined>((best, g) => (!best || Math.abs(value(g) - wanted) < Math.abs(value(best) - wanted) ? g : best), undefined);
      keepFocus(`${id}-seg-${moved?.index ?? segment.index}`);
      setRoute(next);
    } else if (e.key === "Escape") { e.preventDefault(); setRoute(null); }
    else if (e.key === "Tab") pendingFocus = null;
  };
  return (
    <>
      <BaseEdge id={id} path={roundedPath(points, 10, sharp)} markerEnd={markerEnd} style={style} label={label} labelX={mid.x} labelY={mid.y} />
      {editable && segments.map((g) => (
        <path key={`hit-${g.index}`} d={`M ${g.from.x} ${g.from.y} L ${g.to.x} ${g.to.y}`} className="edge-seg-hit nodrag nopan" style={{ cursor: g.orientation === "v" ? "ew-resize" : "ns-resize" }} onPointerDown={grab(g)} />
      ))}
      {editable && selected && (
        <EdgeLabelRenderer>
          {segments.map((g) => <SegmentHandle key={g.index} id={`${id}-seg-${g.index}`} segment={g} name={name} onPointerDown={grab(g)} onKeyDown={keyboard(g)} />)}
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
  onRouteEdge?: (from: string, to: string, route: Coords | null) => void;
  /** Une note a été redimensionnée à la souris : sa nouvelle position et sa nouvelle taille. */
  onNoteResize?: (id: string, r: { x: number; y: number; w: number; h: number }) => void;
  /** Un point de jonction posé à la main a été déplacé (centre dans le repère du schéma). */
  onJunctionMove?: (id: string, x: number, y: number) => void;
  /** Un point de jonction automatique vient d'être déplacé : il devient un point posé à la main. */
  onJunctionCreate?: (spec: { node: string; role: "in" | "out"; face: Face; x: number; y: number; flip?: boolean }) => void;
  /** Double-clic (ou Entrée) sur un point de jonction posé : il s'inverse. */
  onJunctionFlip?: (id: string) => void;
  onConnect?: (from: string, to: string, sf?: Face, tf?: Face) => void;
  /** Une extrémité d'un lien a été reprise : vers une autre face de la même étape (seules les faces changent) ou vers une autre étape. */
  onReconnectEdge?: (old: { from: string; to: string }, next: { from: string; to: string; sf?: Face; tf?: Face }) => void;
  onDeleteNodes?: (ids: string[]) => void;
  onDeleteEdges?: (edges: { from: string; to: string }[]) => void;
  onDropItem?: (item: PaletteItem, pos: { x: number; y: number }) => void;
}

/** Le rectangle de l'icône d'une étape (ou du rond Début / Fin) dans le repère du schéma : c'est là que sont posés les points de connexion. */
/** L'écart (en unités du canevas) en dessous duquel une étape déplacée « tombe » à l'alignement de l'étape à laquelle elle est reliée : un lien presque droit devient droit. */
const ALIGN_PX = 14;
/** La position corrigée d'une étape qu'on déplace en `at` : si le centre de son icône n'est qu'à quelques pixels de celui d'une étape reliée (en hauteur, ou en largeur pour un lien vertical),
 *  elle se cale dessus pour que le lien soit droit, sans le petit décrochage. Un écart nul, ou plus grand que le seuil, ne change rien. `moving` : les étapes déplacées en même temps (elles gardent leurs écarts). */
function alignedAt(id: string, at: Pt, nodes: Node[], links: { source: string; target: string }[], scale: number, moving: Set<string>): Pt {
  const me = nodes.find((n) => n.id === id);
  if (!me || (me.type !== "step" && me.type !== "start" && me.type !== "end")) return at;
  const mine = iconRect({ ...me, position: at }, scale);
  const cx = mine.x + mine.w / 2;
  const cy = mine.y + mine.h / 2;
  const limit = ALIGN_PX * scale;
  let dx = 0;
  let dy = 0;
  let bestX = limit + 1;
  let bestY = limit + 1;
  for (const l of links) {
    const other = l.source === id ? l.target : l.target === id ? l.source : null;
    const node = other ? nodes.find((n) => n.id === other) : undefined;
    if (!node || moving.has(node.id) || (node.type !== "step" && node.type !== "start" && node.type !== "end")) continue;
    const r = iconRect(node, scale);
    const ex = r.x + r.w / 2 - cx;
    const ey = r.y + r.h / 2 - cy;
    if (Math.abs(ey) < bestY) { bestY = Math.abs(ey); dy = ey; }
    if (Math.abs(ex) < bestX) { bestX = Math.abs(ex); dx = ex; }
  }
  return { x: bestX <= limit ? at.x + dx : at.x, y: bestY <= limit ? at.y + dy : at.y };
}

function iconRect(n: Node, scale: number): Rect {
  if (n.type === "step") {
    const icon = 56 * scale;
    return { x: n.position.x + ((n.measured?.width ?? 130 * scale) - icon) / 2, y: n.position.y, w: icon, h: icon };
  }
  const size = 44 * scale;
  return { x: n.position.x, y: n.position.y, w: size, h: size };
}

/** Choisit les faces de chaque lien d'après la position **actuelle** des étapes (donc pendant qu'on les déplace) : la face choisie par la personne en
 *  reliant les étapes est gardée, sinon la paire de faces qui donne la ligne la plus courte et la plus lisible. */
/** Un point de jonction à afficher : posé à la main (`auto: false`, il vit dans le graphe) ou automatique (des liens partagent un point de connexion et rien n'a
 *  encore été déplacé : il se pose tout seul au tronc commun, et devient posé à la main au premier déplacement). `center` : son centre dans le repère du schéma. */
interface JunctionSpec { id: string; node: string; role: "in" | "out"; face: Face; center: Pt; auto: boolean; flip: boolean }
/** Rayon d'un point de jonction (le rond fait 20 px d'interface de diamètre). */
const JUNCTION_PX = 10;
const portKey = (role: "in" | "out", node: string, face: Face) => `${role === "out" ? "o" : "i"}:${node}:${face}`;

/** Choisit les faces de chaque lien d'après la position **actuelle** des étapes (donc pendant qu'on les déplace) : la face choisie par la personne en
 *  reliant les étapes est gardée, sinon la paire de faces qui donne la ligne la plus courte et la plus lisible. Repère aussi les liens qui partagent un point de connexion
 *  (leur point de jonction) et les fait passer par lui quand il a été posé ou déplacé. */
function withPorts(base: Edge[], nodes: Node[], scale: number, snap: boolean, stored: Junction[], jPos: Record<string, Pt>): { edges: Edge[]; junctions: JunctionSpec[] } {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const gap = PORT_GAP_PX * scale;
  const R = JUNCTION_PX * scale;
  const placed = base.flatMap((e) => {
    const a = byId.get(e.source);
    const b = byId.get(e.target);
    if (!a || !b) return [];
    const boxes = [iconRect(a, scale), iconRect(b, scale)] as [Rect, Rect];
    const data = e.data as RoutedData;
    return [{ e, boxes, data, ports: data.pinned ?? autoPorts(boxes[0], boxes[1], gap) }];
  });
  // combien de liens partent de chaque point de sortie, et arrivent à chaque point d'entrée : au-delà d'un, ils partagent un tronc et un point de jonction
  const uses = new Map<string, number>();
  const bump = (key: string) => uses.set(key, (uses.get(key) ?? 0) + 1);
  for (const { e, ports } of placed) { bump(portKey("out", e.source, ports.sf)); bump(portKey("in", e.target, ports.tf)); }
  const hinted = placed.map(({ e, boxes, ports }) => {
    const sharedOut = (uses.get(portKey("out", e.source, ports.sf)) ?? 0) > 1;
    const sharedIn = (uses.get(portKey("in", e.target, ports.tf)) ?? 0) > 1;
    return { sharedOut, sharedIn, hint: junctionCoords(facePoint(boxes[0], ports.sf, "out", gap), facePoint(boxes[1], ports.tf, "in", gap), ports, boxes, sharedOut, sharedIn) };
  });
  // les points de jonction : ceux posés (tant qu'un lien passe par le point de connexion), puis les automatiques (au moins deux liens, tous en « Z » à tronc commun)
  const specs = new Map<string, JunctionSpec>();
  const storedByKey = new Map(stored.map((j) => [portKey(j.role, j.node, j.face), j]));
  const centreOf = (id: string, fallback: Pt): Pt => jPos[id] ?? fallback;
  for (const [key, j] of storedByKey) if ((uses.get(key) ?? 0) > 0) specs.set(key, { id: j.id, node: j.node, role: j.role, face: j.face, center: centreOf(j.id, { x: j.x, y: j.y }), auto: false, flip: !!j.flip });
  placed.forEach(({ e, boxes, ports }) => {
    for (const role of ["out", "in"] as const) {
      const node = role === "out" ? e.source : e.target;
      const face = role === "out" ? ports.sf : ports.tf;
      const key = portKey(role, node, face);
      if (specs.has(key) || storedByKey.has(key) || (uses.get(key) ?? 0) < 2) continue;
      const members = placed.map((m, k) => ({ m, k })).filter(({ m }) => (role === "out" ? m.e.source === node && m.ports.sf === face : m.e.target === node && m.ports.tf === face));
      if (members.some(({ k }) => !hinted[k]?.hint)) continue; // un lien qui n'a pas de tronc commun : pas de point automatique
      const port = role === "out" ? facePoint(boxes[0], face, "out", gap) : facePoint(boxes[1], face, "in", gap);
      const id = `__auto:${key}`;
      specs.set(key, { id, node, role, face, center: centreOf(id, trunkPoint(port, face)), auto: true, flip: false });
    }
  });
  const edges = placed.map(({ e, boxes, data, ports }, i) => {
    const h = hinted[i] as (typeof hinted)[number];
    const outJ = specs.get(portKey("out", e.source, ports.sf));
    const inJ = specs.get(portKey("in", e.target, ports.tf));
    // un point automatique qu'on n'a pas touché ne change pas le tracé (le tronc commun passe déjà par lui) ; posé ou en cours de déplacement, tous ses liens le traversent
    const moved = (j: JunctionSpec | undefined) => !!j && (!j.auto || !!jPos[j.id]);
    const via: Via[] = [outJ, inJ].filter((j): j is JunctionSpec => moved(j)).map((j) => ({ ...j.center, flip: j.flip }));
    return { ...e, sourceHandle: `o:${ports.sf}`, targetHandle: `i:${ports.tf}`, data: { ...data, boxes, snap, hint: h.hint, via, viaR: R, sharpOut: !!outJ?.auto && h.sharedOut && !via.length, sharpIn: !!inJ?.auto && h.sharedIn && !via.length } };
  });
  return { edges, junctions: [...specs.values()] };
}

const SNAP_KEY = "fluxonata.schema.snap";
/** La préférence de l'aimant se garde dans le navigateur (confort de la personne, pas une donnée du workflow) ; absente ou illisible, il est éteint. */
function readSnap(): boolean {
  try { return localStorage.getItem(SNAP_KEY) === "1"; } catch { return false; }
}
function writeSnap(on: boolean) {
  try { localStorage.setItem(SNAP_KEY, on ? "1" : "0"); } catch { /* stockage indisponible : la préférence ne sera pas gardée */ }
}
const snapTo = (v: number) => Math.round(v / GRID) * GRID;

function Magnet() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d="M5 3v9a7 7 0 0014 0V3h-4.5v9a2.5 2.5 0 01-5 0V3z" />
      <path d="M5 7.5h4.5M14.5 7.5H19" />
    </svg>
  );
}

function Inner({ graph, issues, selectedId, onSelect, focus, onOpen, readOnly, statuses, counts, onMove, onRouteEdge, onNoteResize, onJunctionMove, onJunctionCreate, onJunctionFlip, onConnect, onReconnectEdge, onDeleteNodes, onDeleteEdges, onDropItem }: Props) {
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
  const [snap, setSnap] = useState(readSnap); // l'aimant : étapes et segments de lignes calés sur la grille
  const resizeNote = useRef((_id: string, _r: NoteRect) => {});
  resizeNote.current = (nid, r) => onNoteResize?.(nid, snap ? { x: snapTo(r.x), y: snapTo(r.y), w: snapTo(r.w), h: snapTo(r.h) } : { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.w), h: Math.round(r.h) });
  const toggleSnap = () => setSnap((on) => { writeSnap(!on); return !on; });
  const { nodes: graphNodes, edges: baseEdges } = useMemo(() => {
    // a draft written by the AI (or imported) may have no positions: lay them out for display, the saved graph is untouched
    const shown = placeNewNodes(graph);
    const ui = shown.nodes.map((n) => n.ui ?? { x: 0, y: 0 });
    const minX = Math.min(0, ...ui.map((p) => p.x));
    const maxX = Math.max(0, ...ui.map((p) => p.x));
    const hasIn = new Set(graph.edges.map((e) => e.to));
    const hasOut = new Set(graph.edges.map((e) => e.from));
    const entries = graph.nodes.filter((n) => !hasIn.has(n.id) && !graph.detached?.start?.includes(n.id));
    const sinks = graph.nodes.filter((n) => !hasOut.has(n.id) && !graph.detached?.end?.includes(n.id));
    const startY = shown.nodes.find((n) => n.id === entries[0]?.id)?.ui?.y ?? 0;
    const rfNodes: Node[] = [
      { id: "__start__", type: "start", position: graph.layout?.start ?? { x: minX - 120, y: startY + 6 }, draggable: !readOnly, selectable: false, data: {} },
      ...shown.nodes.map((n) => ({
        id: n.id, type: "step", position: n.ui ?? { x: 0, y: 0 }, selected: n.id === selectedId, draggable: !readOnly,
        data: { node: n, errors: issues.filter((i) => i.node === n.id && i.level === "error").length, warnings: issues.filter((i) => i.node === n.id && i.level === "warning").length, runStatus: statuses?.[n.id], runCount: counts?.[n.id] } as StepData,
      })),
      ...(shown.notes ?? []).map((n) => {
        const w = noteSide(n.ui?.w, NOTE_MIN_W);
        const h = noteSide(n.ui?.h, NOTE_MIN_H);
        return {
          id: n.id, type: "note", position: n.ui ? { x: n.ui.x, y: n.ui.y } : { x: 0, y: -140 }, selected: n.id === selectedId, draggable: !readOnly, zIndex: -1, // toujours en arrière-plan
          ...(w || h ? { style: { ...(w ? { width: w } : {}), ...(h ? { height: h } : {}) } } : {}),
          data: { text: n.text, sized: !!(w || h), color: n.color, opacity: n.opacity, editable: !readOnly, onResize: (nid: string, r: NoteRect) => resizeNote.current(nid, r) } as NoteData,
        };
      }),
      { id: "__end__", type: "end", position: graph.layout?.end ?? { x: maxX + 220, y: (shown.nodes.find((n) => n.id === sinks[0]?.id)?.ui?.y ?? 0) + 6 }, draggable: !readOnly, selectable: false, data: {} },
    ];
    const mark = { type: MarkerType.ArrowClosed };
    const rfEdges: Edge[] = [
      ...entries.map((n) => ({ id: `s->${n.id}`, source: START_ID, target: n.id, markerEnd: mark, type: "routed", selectable: !readOnly, deletable: !readOnly, reconnectable: false, selected: edgeId === `s->${n.id}`, data: { boxes: [], editable: false, name: n.name, onRoute: () => {} } as RoutedData })),
      ...graph.edges.map((e) => ({
        id: `${e.from}->${e.to}`, source: e.from, target: e.to, markerEnd: mark, type: "routed", reconnectable: !readOnly, selected: edgeId === `${e.from}->${e.to}`,
        data: { route: e.ui?.route ?? (e.ui?.cx !== undefined ? [e.ui.cx] : undefined), pinned: e.ui?.sf && e.ui?.tf ? { sf: e.ui.sf, tf: e.ui.tf } : undefined, boxes: [], editable: !readOnly, name: graph.nodes.find((n) => n.id === e.to)?.name ?? e.to, onRoute: (route: Coords | null) => onRouteEdge?.(e.from, e.to, route) } as RoutedData,
        label: e.label ? edgeText(e.label) : undefined, className: e.label === "error" ? "edge-error" : undefined,
        style: e.label === "error" ? { strokeDasharray: "6 4" } : undefined,
      })),
      ...sinks.map((n) => ({ id: `${n.id}->e`, source: n.id, target: END_ID, markerEnd: mark, type: "routed", selectable: !readOnly, deletable: !readOnly, reconnectable: false, selected: edgeId === `${n.id}->e`, data: { boxes: [], editable: false, name: "Fin", onRoute: () => {} } as RoutedData })),
    ];
    return { nodes: rfNodes, edges: rfEdges };
  }, [graph, issues, selectedId, readOnly, statuses, counts, onRouteEdge, edgeId]);

  // React Flow works on a local copy of the nodes: positions follow the pointer WHILE dragging; the graph (draft, undo
  // history, autosave) is updated once, when the node is dropped.
  const [nodes, setNodes] = useState<Node[]>(graphNodes);
  const prevSelected = useRef(selectedId);
  const keepSel = useRef(false); // la sélection multiple vient de se former : le retrait de l'étape ouverte dans le panneau ne doit pas la défaire
  const linksRef = useRef<{ source: string; target: string }[]>([]);
  const scaleRef = useRef(1);
  const dragging = useRef(false); // a re-render of the page must never snap a node back while it is being dragged
  // The nodes are rebuilt at every render of the page (autosave, validation…): keep the sizes already measured, otherwise React Flow forgets where
  // the connection points are, takes the lines off and puts them back after measuring again (which also recreates their handles).
  useEffect(() => {
    if (dragging.current) return;
    // La sélection multiple (Maj + clic, ou Maj + glisser sur le fond) vit dans React Flow : on la garde tant que l'étape sélectionnée par la page ne change pas.
    const keep = prevSelected.current === selectedId || keepSel.current;
    keepSel.current = false;
    prevSelected.current = selectedId;
    setNodes((prev) => graphNodes.map((n) => { const old = prev.find((p) => p.id === n.id); const m = old?.measured ? { ...n, measured: old.measured } : n; return keep && old ? { ...m, selected: old.selected } : m; }));
  }, [graphNodes, selectedId]);
  // Les points de jonction ne sont pas dans l'état des étapes : ils se déduisent du graphe et des liens (voir `withPorts`). On ne garde ici que la position de ceux
  // qu'on est en train de déplacer, pour que leurs liens les suivent en direct.
  const [jPos, setJPos] = useState<Record<string, Pt>>({});
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    const live: Record<string, Pt> = {};
    const rest: NodeChange[] = [];
    for (const c of changes) {
      if (!isJunctionId(c.type === "add" ? c.item.id : "id" in c ? c.id : "")) rest.push(c);
      else if (c.type === "position" && c.position) live[c.id] = c.position;
    }
    if (Object.keys(live).length) setJPos((p) => ({ ...p, ...live }));
    if (rest.length) {
      const moving = new Set(rest.flatMap((c) => (c.type === "position" && c.dragging ? [c.id] : [])));
      setNodes((ns) => applyNodeChanges(rest.map((c) => (c.type === "position" && c.dragging && c.position && moving.size === 1 ? { ...c, position: alignedAt(c.id, c.position, ns, linksRef.current, scaleRef.current, moving) } : c)), ns));
    }
  }, []);
  const scale = useMemo(() => scaleFactor(), []);
  scaleRef.current = scale;
  linksRef.current = baseEdges;
  const { edges, junctions } = useMemo(() => withPorts(baseEdges, nodes, scale, snap, graph.junctions ?? [], jPos), [baseEdges, nodes, scale, snap, graph.junctions, jPos]);
  const junctionInfo = useMemo(() => new Map(junctions.map((j) => [j.id, j])), [junctions]);
  const D = 2 * JUNCTION_PX * scale;
  /** Inverser un point de jonction : posé, il s'inverse ; automatique, il est posé là où il est, déjà inversé. */
  const flipJunctionNode = useCallback((id: string) => {
    const j = junctionInfo.get(id);
    if (!j || readOnly) return;
    if (j.auto) onJunctionCreate?.({ node: j.node, role: j.role, face: j.face, x: j.center.x, y: j.center.y, flip: true });
    else onJunctionFlip?.(id);
  }, [junctionInfo, readOnly, onJunctionCreate, onJunctionFlip]);
  const junctionNodes: Node[] = useMemo(
    () => junctions.map((j) => ({
      id: j.id, type: "junction", position: j.center, origin: [0.5, 0.5] as [number, number], selected: j.id === selectedId, draggable: !readOnly, selectable: !j.auto && !readOnly, deletable: !j.auto && !readOnly,
      width: D, height: D, measured: { width: D, height: D }, data: { role: j.role, auto: j.auto, flip: j.flip } as JunctionData,
    })),
    [junctions, selectedId, readOnly, D],
  );
  const allNodes = useMemo(() => [...nodes, ...junctionNodes], [nodes, junctionNodes]);
  /** Une liaison tirée depuis ou vers un point de jonction : une sortie rouge d'un point « départ » ajoute un lien qui part de la même étape par le même point de connexion ; une
   *  entrée verte d'un point « arrivée » ajoute un lien qui arrive à la même étape par le même point de connexion. Toute autre combinaison est refusée. */
  const resolveConnection = useCallback((c: { source: string | null; target: string | null; sourceHandle?: string | null; targetHandle?: string | null }): { from: string; to: string; sf?: Face; tf?: Face } | null => {
    if (!c.source || !c.target || c.source === c.target) return null;
    if (c.source === START_ID) return !c.target.startsWith("__") && !junctionInfo.has(c.target) && canLinkStart(graph, c.target) ? { from: START_ID, to: c.target } : null; // Début → une étape sans entrée
    if (c.target === END_ID) return !c.source.startsWith("__") && !junctionInfo.has(c.source) && canLinkEnd(graph, c.source) ? { from: c.source, to: END_ID } : null; // une étape sans sortie → Fin
    const sj = junctionInfo.get(c.source);
    const tj = junctionInfo.get(c.target);
    if (sj && tj) return null;
    if (sj) return sj.role === "out" && !c.target.startsWith("__") ? { from: sj.node, to: c.target, sf: sj.face, tf: faceOfHandle(c.targetHandle) } : null;
    if (tj) return tj.role === "in" && !c.source.startsWith("__") ? { from: c.source, to: tj.node, sf: faceOfHandle(c.sourceHandle), tf: tj.face } : null;
    return c.source.startsWith("__") || c.target.startsWith("__") ? null : { from: c.source, to: c.target, sf: faceOfHandle(c.sourceHandle), tf: faceOfHandle(c.targetHandle) };
  }, [junctionInfo, graph]);

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: zone de dépôt (glisser-déposer depuis la palette) ; au clavier, la palette ajoute les étapes sans glisser
    <div ref={wrap} className="flow-drop" onKeyDown={(e) => { if (e.key === "Enter" && selectedId && isJunctionId(selectedId)) { e.preventDefault(); flipJunctionNode(selectedId); } }} onDragOver={(e) => { if (e.dataTransfer.types.includes(DRAG_MIME)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } }}
      onDrop={(e) => {
        const raw = e.dataTransfer.getData(DRAG_MIME);
        if (!raw || readOnly || !onDropItem) return;
        e.preventDefault();
        const at = screenToFlowPosition({ x: e.clientX, y: e.clientY });
        onDropItem(JSON.parse(raw) as PaletteItem, snap ? { x: snapTo(at.x), y: snapTo(at.y) } : at);
      }}>
      <ReactFlow
        nodes={allNodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onNodesChange={onNodesChange} fitView fitViewOptions={{ padding: 0.15, maxZoom: 1, ...(readOnly ? { minZoom: 0.02 } : {}) }} minZoom={readOnly ? 0.02 : 0.2} maxZoom={2}
        onMoveStart={(e) => { if (e) userMoved.current = true; }}
        snapToGrid={snap} snapGrid={[GRID, GRID]} nodesConnectable={!readOnly} edgesReconnectable={!readOnly} connectionLineType={ConnectionLineType.SmoothStep} connectionRadius={28} elementsSelectable zoomOnDoubleClick={false}
        multiSelectionKeyCode={["Shift", "Control", "Meta"]} onSelectionChange={({ nodes: sel }) => { if (sel.length > 1 && selectedId !== null) { keepSel.current = true; onSelect(null); } }} onNodeClick={(ev, n) => { setEdgeId(null); if (ev.shiftKey || ev.metaKey || ev.ctrlKey) return; /* ajout à la sélection : pas de panneau */ if (!n.id.startsWith("__") || readOnly) onSelect(n.id); }} onPaneClick={() => { onSelect(null); setEdgeId(null); }} onEdgeClick={(_, e) => { onSelect(null); setEdgeId(e.id); }}
        onNodeDoubleClick={(_, n) => { if (isJunctionId(n.id)) flipJunctionNode(n.id); else if (!n.id.startsWith("__")) onOpen?.(n.id); }}
        onNodeDragStart={() => { dragging.current = true; }}
        onNodeDragStop={(_, n, dragged) => {
          const all = dragged.length ? dragged : [n];
          const moved = all.filter((d) => !isJunctionId(d.id));
          for (const d of all.filter((x) => isJunctionId(x.id))) {
            const spec = junctionInfo.get(d.id);
            const at = { x: d.position.x, y: d.position.y }; // l'origine du rond est son centre
            if (spec?.auto) onJunctionCreate?.({ node: spec.node, role: spec.role, face: spec.face, x: at.x, y: at.y });
            else onJunctionMove?.(d.id, at.x, at.y);
            setJPos((p) => { const { [d.id]: _gone, ...rest } = p; void _gone; return rest; });
          }
          const together = new Set(all.map((d) => d.id));
          const place = (d: Node) => (all.length === 1 ? alignedAt(d.id, d.position, nodes, linksRef.current, scaleRef.current, together) : d.position); // une seule étape : elle se cale ; plusieurs : leurs écarts sont gardés
          if (moved.length) onMove?.(moved.map((d) => { const at = place(d); return { id: d.id, x: Math.round(at.x), y: Math.round(at.y) }; }));
          dragging.current = false;
        }}
        isValidConnection={(c) => !!resolveConnection(c)}
        onConnect={(c: Connection) => {
          const r = resolveConnection(c);
          if (r && (r.from === START_ID || r.to === END_ID || !wouldCreateCycle(graph, r.from, r.to))) onConnect?.(r.from, r.to, r.sf, r.tf);
        }}
        onReconnect={(old: Edge, c: Connection) => {
          if (!c.source || !c.target || c.source.startsWith("__") || c.target.startsWith("__") || c.source === c.target) return;
          const moved = c.source !== old.source || c.target !== old.target;
          if (moved && wouldCreateCycle({ ...graph, edges: graph.edges.filter((x) => !(x.from === old.source && x.to === old.target)) }, c.source, c.target)) return;
          onReconnectEdge?.({ from: old.source, to: old.target }, { from: c.source, to: c.target, sf: faceOfHandle(c.sourceHandle), tf: faceOfHandle(c.targetHandle) });
        }}
        onNodesDelete={(ns) => onDeleteNodes?.(ns.map((n) => n.id).filter((i) => !i.startsWith("__")))}
        onEdgesDelete={(es) => onDeleteEdges?.(es.filter((e) => e.source === START_ID || e.target === END_ID || (!e.source.startsWith("__") && !e.target.startsWith("__"))).map((e) => ({ from: e.source, to: e.target })))}
        deleteKeyCode={readOnly ? null : ["Delete", "Backspace"]} nodesFocusable edgesFocusable proOptions={{ hideAttribution: true }}
        aria-label="Schéma du workflow. Une vue en liste équivalente, entièrement utilisable au clavier, est disponible."
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1.2} />
        <Controls showInteractive={false}>
          {!readOnly && (
            <ControlButton onClick={toggleSnap} title={snap ? "Aimant actif : les étapes et les lignes se calent sur la grille (cliquer pour le désactiver)" : "Aimanter à la grille : caler les étapes et les lignes sur la grille"} aria-label="Aimanter à la grille" aria-pressed={snap} className="snap-toggle">
              <Magnet />
            </ControlButton>
          )}
        </Controls>
      </ReactFlow>
    </div>
  );
}

export function CanvasView(props: Props) {
  return <div className="canvas"><ReactFlowProvider><Inner {...props} /></ReactFlowProvider></div>;
}
