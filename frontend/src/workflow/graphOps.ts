import type { Action, JsonSchema } from "../api";
import type { EdgeLabel, Graph, GraphEdge, GraphNode, Issue, NodeType } from "./types";
import { NODE_LABELS } from "./types";

export const emptyGraph = (): Graph => ({ inputs: { type: "object", properties: {}, required: [] }, variables: [], form: null, notes: [], nodes: [], edges: [], outputs: {} });

export function uniqueId(graph: Graph, base: string): string {
  const root = base.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^[0-9]/, "n$&").slice(0, 24) || "etape";
  const ids = new Set(graph.nodes.map((n) => n.id));
  if (!ids.has(root)) return root;
  let i = 2;
  while (ids.has(`${root}_${i}`)) i++;
  return `${root}_${i}`;
}

export function defaultNode(graph: Graph, type: NodeType, action?: Action): GraphNode {
  const base = type === "action" && action ? action.slug : type;
  const node: GraphNode = {
    id: uniqueId(graph, base),
    type,
    name: type === "action" && action ? action.name : NODE_LABELS[type],
  };
  if (type === "action" && action) {
    node.action = action.slug;
    node.inputs = {};
    const props = action.latest_version?.input_schema.properties ?? {};
    for (const [k, p] of Object.entries(props)) if (p.default !== undefined) node.inputs[k] = p.default;
  }
  if (type === "condition") node.expr = "";
  if (type === "wait") node.seconds = 60;
  if (type === "approval") node.title = "";
  if (type === "foreach") Object.assign(node, { max_iterations: 10, concurrency: 1, item_input: "item", items: "" });
  if (type === "script") Object.assign(node, { language: "python", script: "# Écrivez votre code ici. Les entrées liées sont des variables ; déclarez vos sorties dans l'onglet Sorties.\nresultat = \"ok\"\nSystem.log(\"terminé\")\n", bindings: {}, outputs: [{ name: "resultat", type: "string" }], timeout_seconds: 60 });
  if (type === "throw") Object.assign(node, { code: "ERREUR", message: "" });
  if (type === "ai") Object.assign(node, { provider: "", prompt: "", output_schema: { type: "object", required: ["resultat"], properties: { resultat: { type: "string", title: "Résultat" } } } });
  return node;
}

export const GRID_X = 200; // horizontal flow: left to right
export const GRID_Y = 120;

/** New nodes go to the right of the selected one (or after the last node), avoiding an occupied slot. */
function placeNext(graph: Graph, afterId?: string): { x: number; y: number } {
  const after = graph.nodes.find((n) => n.id === afterId);
  const taken = new Set(graph.nodes.map((n) => `${n.ui?.x}:${n.ui?.y}`));
  let pos = after?.ui ? { x: after.ui.x + GRID_X, y: after.ui.y } : { x: Math.max(-GRID_X, ...graph.nodes.map((n) => n.ui?.x ?? 0)) + GRID_X, y: 0 };
  while (taken.has(`${pos.x}:${pos.y}`)) pos = { x: pos.x, y: pos.y + GRID_Y };
  return pos;
}

/** Gives a position to the nodes that have none (steps proposed by the AI assistant): right of their first predecessor,
 *  on a free slot. Existing nodes never move. */
export function placeNewNodes(graph: Graph): Graph {
  const placed: GraphNode[] = graph.nodes.filter((n) => n.ui);
  for (const n of graph.nodes.filter((x) => !x.ui)) {
    const pred = graph.edges.find((e) => e.to === n.id && placed.some((p) => p.id === e.from))?.from;
    placed.push({ ...n, ui: placeNext({ ...graph, nodes: placed }, pred) });
  }
  const byId = new Map(placed.map((n) => [n.id, n]));
  const nodes = graph.nodes.map((n) => byId.get(n.id) ?? n);
  // notes without a position go above the schema, side by side
  const top = Math.min(0, ...nodes.map((n) => n.ui?.y ?? 0)) - 140;
  const left = Math.min(0, ...nodes.map((n) => n.ui?.x ?? 0));
  const loose = (graph.notes ?? []).filter((n) => !n.ui);
  const notes = (graph.notes ?? []).map((n) => (n.ui ? n : { ...n, ui: { x: left + loose.indexOf(n) * 220, y: top } }));
  return { ...graph, nodes, ...(graph.notes ? { notes } : {}) };
}

/** Adds a node; when `afterId` is given it is connected from that node with the proper label. */
export function addNode(graph: Graph, node: GraphNode, afterId?: string): Graph {
  const withUi = { ...node, ui: node.ui ?? placeNext(graph, afterId) };
  const next: Graph = { ...graph, nodes: [...graph.nodes, withUi] };
  if (afterId) {
    const src = graph.nodes.find((n) => n.id === afterId);
    if (src) return connect(next, afterId, withUi.id, defaultLabel(next, src));
  }
  return next;
}

export function defaultLabel(graph: Graph, src: GraphNode): EdgeLabel {
  if (src.type !== "condition") return "next";
  const used = graph.edges.filter((e) => e.from === src.id).map((e) => e.label);
  return used.includes("true") ? "false" : "true";
}

export function connect(graph: Graph, from: string, to: string, label: EdgeLabel = "next"): Graph {
  if (from === to) return graph;
  if (graph.edges.some((e) => e.from === from && e.to === to)) return graph;
  const edge: GraphEdge = label === "next" ? { from, to } : { from, to, label };
  return { ...graph, edges: [...graph.edges, edge] };
}

export function disconnect(graph: Graph, from: string, to: string): Graph {
  return { ...graph, edges: graph.edges.filter((e) => !(e.from === from && e.to === to)) };
}

export function relabel(graph: Graph, from: string, to: string, label: EdgeLabel): Graph {
  return {
    ...graph,
    edges: graph.edges.map((e) => (e.from === from && e.to === to ? (label === "next" ? { from, to } : { from, to, label }) : e)),
  };
}

export function removeNode(graph: Graph, id: string): Graph {
  return { ...graph, nodes: graph.nodes.filter((n) => n.id !== id), edges: graph.edges.filter((e) => e.from !== id && e.to !== id) };
}

export function duplicateNode(graph: Graph, id: string): { graph: Graph; newId: string } | null {
  const src = graph.nodes.find((n) => n.id === id);
  if (!src) return null;
  const newId = uniqueId(graph, src.id.replace(/_\d+$/, ""));
  const copy: GraphNode = { ...structuredClone(src), id: newId, name: `${src.name} (copie)`, ui: { x: (src.ui?.x ?? 0) + 60, y: (src.ui?.y ?? 0) + 60 } };
  return { graph: { ...graph, nodes: [...graph.nodes, copy] }, newId };
}

export function updateNode(graph: Graph, id: string, patch: Partial<GraphNode>): Graph {
  return { ...graph, nodes: graph.nodes.map((n) => (n.id === id ? { ...n, ...patch } : n)) };
}

export function moveNode(graph: Graph, id: string, x: number, y: number): Graph {
  return updateNode(graph, id, { ui: { x, y } });
}

/** Topological order, or null when the graph has a cycle. */
export function topoOrder(graph: Graph): string[] | null {
  const indeg = new Map(graph.nodes.map((n) => [n.id, 0]));
  for (const e of graph.edges) indeg.set(e.to, (indeg.get(e.to) ?? 0) + 1);
  const queue = graph.nodes.filter((n) => (indeg.get(n.id) ?? 0) === 0).map((n) => n.id);
  const order: string[] = [];
  while (queue.length) {
    const cur = queue.shift() as string;
    order.push(cur);
    for (const e of graph.edges) {
      if (e.from !== cur) continue;
      const d = (indeg.get(e.to) ?? 0) - 1;
      indeg.set(e.to, d);
      if (d === 0) queue.push(e.to);
    }
  }
  return order.length === graph.nodes.length ? order : null;
}

export function wouldCreateCycle(graph: Graph, from: string, to: string): boolean {
  return topoOrder({ ...graph, edges: [...graph.edges, { from, to }] }) === null;
}

export function ancestors(graph: Graph, id: string): Set<string> {
  const result = new Set<string>();
  const stack = [id];
  while (stack.length) {
    const cur = stack.pop() as string;
    for (const e of graph.edges) if (e.to === cur && !result.has(e.from)) { result.add(e.from); stack.push(e.from); }
  }
  return result;
}

/** Layered auto-layout (longest path from the entries), no external dependency. */
export function autoLayout(graph: Graph): Graph {
  const order = topoOrder(graph) ?? graph.nodes.map((n) => n.id);
  const level = new Map<string, number>();
  for (const id of order) {
    const parents = graph.edges.filter((e) => e.to === id).map((e) => level.get(e.from) ?? 0);
    level.set(id, parents.length ? Math.max(...parents) + 1 : 0);
  }
  const perLevel = new Map<number, number>();
  return {
    ...graph,
    nodes: graph.nodes.map((n) => {
      const l = level.get(n.id) ?? 0;
      const idx = perLevel.get(l) ?? 0;
      perLevel.set(l, idx + 1);
      return { ...n, ui: { x: l * GRID_X, y: idx * GRID_Y } };
    }),
  };
}

export type OutputField = { path: string; label: string; type?: string | string[] };

function fieldsOfNode(n: GraphNode, actions: Action[]): OutputField[] {
  if (n.type === "action") {
    const act = actions.find((a) => a.slug === n.action);
    const props: Record<string, JsonSchema> = act?.latest_version?.output_schema.properties ?? {};
    return Object.entries(props).map(([k, p]) => ({ path: `steps.${n.id}.output.${k}`, label: `${n.name} → ${p.title ?? k}`, type: p.type }));
  }
  if (n.type === "condition") return [{ path: `steps.${n.id}.output.result`, label: `${n.name} → résultat`, type: "boolean" }];
  if (n.type === "approval") return [{ path: `steps.${n.id}.output.approved`, label: `${n.name} → approuvé`, type: "boolean" }];
  if (n.type === "foreach") return [{ path: `steps.${n.id}.output.results`, label: `${n.name} → résultats`, type: "array" }];
  if (n.type === "script") return (n.outputs ?? []).map((o) => ({ path: `steps.${n.id}.output.${o.name}`, label: `${n.name} → ${o.name}`, type: o.type }));
  if (n.type === "ai") return Object.entries(n.output_schema?.properties ?? {}).map(([k, p]) => ({ path: `steps.${n.id}.output.${k}`, label: `${n.name} → ${p.title ?? k}`, type: p.type }));
  return [];
}

function baseFields(graph: Graph): OutputField[] {
  const inputs = Object.entries(graph.inputs.properties ?? {}).map(([k, p]) => ({ path: `inputs.${k}`, label: `Entrée « ${p.title ?? k} »`, type: p.type }));
  const vars = (graph.variables ?? []).map((v) => ({ path: `vars.${v.name}`, label: `Variable « ${v.name} »`, type: v.type as string }));
  return [...inputs, ...vars];
}

/** Variables a node may reference: workflow inputs/variables + outputs of the steps upstream of it. */
export function availableVariables(graph: Graph, nodeId: string, actions: Action[]): OutputField[] {
  const up = ancestors(graph, nodeId);
  return [...baseFields(graph), ...graph.nodes.filter((n) => up.has(n.id)).flatMap((n) => fieldsOfNode(n, actions))];
}

/** Every value available at the end of the workflow (used for the workflow outputs). */
export function allVariables(graph: Graph, actions: Action[]): OutputField[] {
  return [...baseFields(graph), ...graph.nodes.flatMap((n) => fieldsOfNode(n, actions))];
}

export function typeCompatible(source?: string | string[], target?: string | string[]): boolean {
  if (!source || !target) return true;
  const s = new Set(Array.isArray(source) ? source : [source]);
  const t = new Set(Array.isArray(target) ? target : [target]);
  if (s.has("integer")) s.add("number");
  return [...s].some((x) => t.has(x));
}

const isEmpty = (v: unknown) => v === undefined || v === null || v === "";

/** Instant client-side checks (the server remains authoritative). */
export function clientValidate(graph: Graph, actions: Action[]): Issue[] {
  const issues: Issue[] = [];
  if (graph.nodes.length === 0) return issues;
  if (topoOrder(graph) === null)
    issues.push({ node: null, level: "error", code: "cycle", message: "Le workflow contient un cycle. Utilisez une étape « Pour chaque » pour répéter." });
  for (const n of graph.nodes) {
    if (!n.name.trim()) issues.push({ node: n.id, level: "warning", code: "missing_name", message: "Donnez un nom à cette étape." });
    const incoming = graph.edges.filter((e) => e.to === n.id).length;
    if (incoming > 1 && n.type !== "join")
      issues.push({ node: n.id, level: "error", code: "implicit_join", message: "Plusieurs flux arrivent ici : ajoutez une étape « Jonction »." });
    if (n.type === "action") {
      const act = actions.find((a) => a.slug === n.action);
      if (!act) { issues.push({ node: n.id, level: "error", code: "unknown_action", message: "Action introuvable dans le catalogue." }); continue; }
      const schema = act.latest_version?.input_schema;
      for (const key of schema?.required ?? [])
        if (isEmpty(n.inputs?.[key]))
          issues.push({ node: n.id, level: "error", code: "missing_param", message: `Paramètre obligatoire manquant : ${schema?.properties?.[key]?.title ?? key}.` });
    }
    if (n.type === "script" && !(n.script ?? "").trim()) issues.push({ node: n.id, level: "error", code: "empty_script", message: "Le script est vide." });
    if (n.type === "script" && n.ai_review) issues.push({ node: n.id, level: "error", code: "ai_review_required", message: "Script écrit par l'IA : relisez le code, testez-le, puis confirmez « J'ai relu ce code » dans l'éditeur de script." });
    if (n.type === "throw" && !(n.message ?? "").trim()) issues.push({ node: n.id, level: "warning", code: "missing_message", message: "Indiquez le message de l'exception." });
    if (n.type === "condition" && !(n.expr ?? "").trim()) issues.push({ node: n.id, level: "error", code: "missing_expr", message: "La condition est vide." });
    if (n.type === "ai" && n.skill !== undefined) {
      // library skill: the task is the skill's; its inputs and the provider (node or skill default) are checked by the server
      if (!n.skill.trim()) issues.push({ node: n.id, level: "error", code: "missing_skill", message: "Choisissez un skill de la bibliothèque (ou passez en tâche libre)." });
    } else if (n.type === "ai") {
      if (!(n.prompt ?? "").trim()) issues.push({ node: n.id, level: "error", code: "missing_prompt", message: "Décrivez la tâche confiée à l'IA." });
      if (!(n.provider ?? "").trim()) issues.push({ node: n.id, level: "error", code: "unknown_provider", message: "Choisissez un fournisseur d'IA." });
    }
    if (n.type === "approval" && !(n.title ?? "").trim()) issues.push({ node: n.id, level: "error", code: "missing_title", message: "Indiquez ce qui doit être approuvé." });
    if (n.type === "foreach" && !(n.max_iterations && n.max_iterations >= 1 && n.max_iterations <= 100))
      issues.push({ node: n.id, level: "error", code: "unbounded_loop", message: "Une boucle doit borner ses itérations (1 à 100)." });
  }
  return issues;
}

export function mergeIssues(client: Issue[], server: Issue[]): Issue[] {
  const seen = new Set<string>();
  return [...client, ...server].filter((i) => {
    const key = `${i.node}|${i.code}|${i.message}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** One-line summary shown on the node (business wording, not JSON). */
export function summarize(n: GraphNode, actions: Action[]): string {
  switch (n.type) {
    case "action": {
      const act = actions.find((a) => a.slug === n.action);
      const filled = Object.values(n.inputs ?? {}).filter((v) => !isEmpty(v)).length;
      return `${act?.name ?? n.action ?? "Action ?"} · ${filled} paramètre(s) renseigné(s)`;
    }
    case "condition": return n.expr ? `Si ${n.expr}` : "Condition à définir";
    case "script": return `${n.language === "javascript" ? "JavaScript" : "Python"} · ${(n.script ?? "").split("\n").find((l) => l.trim() && !l.trim().startsWith("#")) ?? "script vide"}`.slice(0, 70);
    case "throw": return n.message ? `Lève : ${n.message}` : "Exception à définir";
    case "wait": return `Attendre ${n.seconds ?? "?"} s`;
    case "approval": return n.title ? `Demander : ${n.title}` : "Approbation à définir";
    case "foreach": return `Pour chaque élément (max ${n.max_iterations ?? "?"})`;
    case "ai": return n.skill ? `Skill IA : ${n.skill} (${n.provider || "fournisseur par défaut"})` : n.prompt ? `IA (${n.provider || "fournisseur ?"}) : ${n.prompt.slice(0, 40)}` : "Étape IA à définir";
    case "subworkflow": return `Appelle ${n.workflow ?? "?"}`;
    default: return "Attend tous les flux entrants";
  }
}
