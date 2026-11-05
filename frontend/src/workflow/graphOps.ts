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
  if (type === "switch") node.cases = [{ id: "cas_1", expr: "" }, { id: "cas_2", expr: "" }];
  if (type === "wait") node.seconds = 60;
  if (type === "transform") Object.assign(node, { op: "filter", items: "", where: "" });
  if (type === "agent") Object.assign(node, { goal: "", tools: [], provider: "", mode: "test", approval: "effectful", max_steps: 5 });
  if (type === "smtp") Object.assign(node, { connection: "", mode: "test", to: "", cc: "", subject: "", body: "" });
  if (type === "sftp") Object.assign(node, { connection: "", mode: "test", operation: "list", path: "/" });
  if (type === "ldap") Object.assign(node, { connection: "", mode: "test", filter: "(objectClass=person)", attributes: ["cn", "mail"], scope: "sub", max_rows: 100 });
  if (type === "sql") Object.assign(node, { connection: "", mode: "test", statement: "SELECT 1 AS n", max_rows: 100, timeout_seconds: 15 });
  if (type === "http") Object.assign(node, { method: "GET", path: "/", connection: "", mode: "test", timeout_seconds: 15 });
  if (type === "approval") node.title = "";
  if (type === "while") Object.assign(node, { workflow: "", inputs: {}, condition: "", max_iterations: 10, delay_seconds: 0 });
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
  if (graph.nodes.length > 0 && graph.nodes.every((n) => !n.ui)) {
    // nothing is placed yet (a draft written by the AI, an import, a workflow published without layout): lay the whole diagram out
    const pos = layeredPositions(graph);
    return placeNewNodes({ ...graph, nodes: graph.nodes.map((n) => ({ ...n, ui: pos.get(n.id) ?? { x: 0, y: 0 } })) });
  }
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
  const used = graph.edges.filter((e) => e.from === src.id).map((e) => e.label);
  if (src.type === "switch") return [...(src.cases ?? []).map((c) => c.id), "default"].find((l) => !used.includes(l)) ?? "default";
  if (src.type !== "condition") return "next";
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
    edges: graph.edges.map((e) => (e.from === from && e.to === to ? { from, to, ...(label === "next" ? {} : { label }), ...(e.ui ? { ui: e.ui } : {}) } : e)),
  };
}

/** Moves the vertical segment of a line (cx), or restores the automatic routing (cx = null). */
export function bendEdge(graph: Graph, from: string, to: string, cx: number | null): Graph {
  return {
    ...graph,
    edges: graph.edges.map((e) => {
      if (e.from !== from || e.to !== to) return e;
      const { ui: _ui, ...rest } = e;
      void _ui;
      return cx === null ? rest : { ...rest, ui: { cx: Math.round(cx) } };
    }),
  };
}

/** Remembers where the person put the virtual « Début » or « Fin » node. */
export function moveEndpoint(graph: Graph, which: "start" | "end", x: number, y: number): Graph {
  return { ...graph, layout: { ...graph.layout, [which]: { x: Math.round(x), y: Math.round(y) } } };
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

/** Layers per band: a longer chain is folded onto the next band (below) so the diagram keeps a readable shape instead of a very long strip. */
export const BAND_LAYERS = 14;

/** Layered layout, no external dependency: layers by longest path from the entries, order inside a layer by barycentre sweeps (fewer
 *  crossings), each node centred on its predecessors (then on its successors), long chains folded onto bands stacked vertically. */
export function layeredPositions(graph: Graph, bandLayers = BAND_LAYERS): Map<string, { x: number; y: number }> {
  const order = topoOrder(graph) ?? graph.nodes.map((n) => n.id);
  const ids = new Set(graph.nodes.map((n) => n.id));
  const preds = new Map<string, string[]>(), succs = new Map<string, string[]>();
  for (const id of ids) { preds.set(id, []); succs.set(id, []); }
  for (const e of graph.edges) if (ids.has(e.from) && ids.has(e.to) && e.from !== e.to) { preds.get(e.to)?.push(e.from); succs.get(e.from)?.push(e.to); }
  const level = new Map<string, number>();
  for (const id of order) level.set(id, Math.max(-1, ...(preds.get(id) ?? []).map((p) => level.get(p) ?? 0)) + 1);
  const depth = Math.max(0, ...level.values()) + 1;
  const layers: string[][] = Array.from({ length: depth }, () => []);
  for (const n of graph.nodes) layers[level.get(n.id) ?? 0]?.push(n.id);

  // 1. order inside each layer: barycentre of the neighbours' rank, a few sweeps down then up
  const rank = new Map<string, number>();
  const rerank = (l: string[]) => l.forEach((id, i) => rank.set(id, i));
  layers.forEach(rerank);
  const mean = (xs: number[], fallback: number) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : fallback);
  const sweep = (idxs: number[], nb: Map<string, string[]>) => {
    for (const li of idxs) {
      const l = layers[li] ?? [];
      const key = new Map(l.map((id) => [id, mean((nb.get(id) ?? []).map((m) => rank.get(m) ?? 0), rank.get(id) ?? 0)]));
      l.sort((a, b) => (key.get(a) ?? 0) - (key.get(b) ?? 0) || (rank.get(a) ?? 0) - (rank.get(b) ?? 0));
      rerank(l);
    }
  };
  const down = layers.map((_, i) => i), up = [...down].reverse();
  for (let i = 0; i < 4; i++) { sweep(down.slice(1), preds); sweep(up.slice(1), succs); }

  // 2. vertical position: centred on the predecessors (the first layer of a band starts afresh), neighbours never closer than GRID_Y
  const y = new Map<string, number>();
  const place = (l: string[], want: (id: string, i: number) => number) => {
    const ws = l.map(want);
    const ys: number[] = [];
    ws.forEach((w, i) => ys.push(i === 0 ? w : Math.max(w, (ys[i - 1] ?? 0) + GRID_Y)));
    const shift = mean(ws.map((w, i) => w - (ys[i] ?? w)), 0); // pushed apart: recentre the layer on what its nodes wanted
    l.forEach((id, i) => y.set(id, (ys[i] ?? 0) + shift));
  };
  layers.forEach((l, li) => place(l, (id, i) => (li % bandLayers === 0 ? i * GRID_Y : mean((preds.get(id) ?? []).filter((p) => y.has(p)).map((p) => y.get(p) ?? 0), i * GRID_Y))));
  for (let li = depth - 2; li >= 0; li--) {
    if (li % bandLayers === bandLayers - 1) continue; // the next layer is on another band
    const l = layers[li] ?? [];
    place(l, (id, i) => { const ss = (succs.get(id) ?? []).filter((m) => (level.get(m) ?? 0) === li + 1); return ss.length ? mean(ss.map((m) => y.get(m) ?? 0), i * GRID_Y) : (y.get(id) ?? i * GRID_Y); });
  }

  // 3. bands: x by layer inside the band, bands stacked below one another
  const out = new Map<string, { x: number; y: number }>();
  let top = 0;
  for (let b = 0; b * bandLayers < depth; b++) {
    const members = layers.slice(b * bandLayers, (b + 1) * bandLayers).flat();
    const lo = Math.min(...members.map((id) => y.get(id) ?? 0)), hi = Math.max(...members.map((id) => y.get(id) ?? 0));
    for (const id of members) out.set(id, { x: ((level.get(id) ?? 0) % bandLayers) * GRID_X, y: Math.round(top + (y.get(id) ?? 0) - lo) });
    top += hi - lo + GRID_Y * 1.5;
  }
  return out;
}

/** « Organiser automatiquement »: a fresh layout (forgets the hand-placed endpoints and bent lines). */
export function autoLayout(graph: Graph): Graph {
  const pos = layeredPositions(graph);
  const { layout: _layout, ...rest } = graph;
  void _layout;
  return {
    ...rest,
    edges: graph.edges.map(({ ui: _ui, ...e }) => { void _ui; return e; }),
    nodes: graph.nodes.map((n) => ({ ...n, ui: pos.get(n.id) ?? { x: 0, y: 0 } })),
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
  if (n.type === "switch") return [{ path: `steps.${n.id}.output.result`, label: `${n.name} → cas retenu`, type: "string" }];
  if (n.type === "transform") return n.op === "set" ? Object.keys(n.fields ?? {}).map((k) => ({ path: `steps.${n.id}.output.${k}`, label: `${n.name} → ${k}` })) : [
    { path: `steps.${n.id}.output.items`, label: `${n.name} → éléments`, type: "array" },
    { path: `steps.${n.id}.output.count`, label: `${n.name} → nombre`, type: "integer" },
  ];
  if (n.type === "agent") return [
    { path: `steps.${n.id}.output.answer`, label: `${n.name} → réponse`, type: "string" },
    { path: `steps.${n.id}.output.iterations`, label: `${n.name} → tours`, type: "integer" },
  ];
  if (n.type === "smtp") return [
    { path: `steps.${n.id}.output.sent`, label: `${n.name} → envoyé`, type: "boolean" },
    { path: `steps.${n.id}.output.recipients`, label: `${n.name} → destinataires`, type: "integer" },
  ];
  if (n.type === "sftp") return [
    { path: `steps.${n.id}.output.content`, label: `${n.name} → contenu` },
    { path: `steps.${n.id}.output.entries`, label: `${n.name} → fichiers`, type: "array" },
  ];
  if (n.type === "ldap") return [
    { path: `steps.${n.id}.output.rows`, label: `${n.name} → entrées`, type: "array" },
    { path: `steps.${n.id}.output.count`, label: `${n.name} → nombre`, type: "integer" },
  ];
  if (n.type === "sql") return [
    { path: `steps.${n.id}.output.rows`, label: `${n.name} → lignes`, type: "array" },
    { path: `steps.${n.id}.output.count`, label: `${n.name} → nombre de lignes`, type: "integer" },
  ];
  if (n.type === "http") return [
    { path: `steps.${n.id}.output.status`, label: `${n.name} → code HTTP`, type: "integer" },
    { path: `steps.${n.id}.output.ok`, label: `${n.name} → succès (2xx)`, type: "boolean" },
    { path: `steps.${n.id}.output.body`, label: `${n.name} → réponse` },
  ];
  if (n.type === "approval") return [{ path: `steps.${n.id}.output.approved`, label: `${n.name} → approuvé`, type: "boolean" }];
  if (n.type === "while") return [
    { path: `steps.${n.id}.output.iterations`, label: `${n.name} → répétitions`, type: "integer" },
    { path: `steps.${n.id}.output.last`, label: `${n.name} → dernier résultat`, type: "object" },
    { path: `steps.${n.id}.output.results`, label: `${n.name} → résultats`, type: "array" },
  ];
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
    if (incoming > 1 && n.type !== "join" && n.type !== "race")
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
    if (n.type === "switch") {
      const cases = n.cases ?? [];
      if (cases.length === 0) issues.push({ node: n.id, level: "error", code: "missing_cases", message: "Un choix multiple a besoin d'au moins un cas." });
      for (const c of cases) if (!c.expr.trim()) issues.push({ node: n.id, level: "error", code: "missing_expr", message: `Le cas « ${c.id} » n'a pas de condition.` });
      if (new Set(cases.map((c) => c.id)).size !== cases.length) issues.push({ node: n.id, level: "error", code: "bad_case", message: "Deux cas ont le même identifiant." });
    }
    if (n.type === "condition" && !(n.expr ?? "").trim()) issues.push({ node: n.id, level: "error", code: "missing_expr", message: "La condition est vide." });
    if (n.type === "ai" && n.skill !== undefined) {
      // library skill: the task is the skill's; its inputs and the provider (node or skill default) are checked by the server
      if (!n.skill.trim()) issues.push({ node: n.id, level: "error", code: "missing_skill", message: "Choisissez un skill de la bibliothèque (ou passez en tâche libre)." });
    } else if (n.type === "ai") {
      if (!(n.prompt ?? "").trim()) issues.push({ node: n.id, level: "error", code: "missing_prompt", message: "Décrivez la tâche confiée à l'IA." });
      if (!(n.provider ?? "").trim()) issues.push({ node: n.id, level: "error", code: "unknown_provider", message: "Choisissez un fournisseur d'IA." });
    }
    if (n.type === "agent" && (!(n.goal ?? "").trim() || (n.tools ?? []).length === 0)) issues.push({ node: n.id, level: "error", code: "bad_agent", message: "Décrivez l'objectif de l'agent et choisissez au moins un outil." });
    if (n.type === "smtp" && !n.connection) issues.push({ node: n.id, level: "error", code: "missing_connection", message: "Choisissez la connexion (type « Serveur SMTP »)." });
    if (n.type === "smtp" && (!(n.to ?? "").trim() || !(n.subject ?? "").trim() || !String(n.body ?? "").trim())) issues.push({ node: n.id, level: "error", code: "bad_smtp", message: "Indiquez le destinataire, l'objet et le corps du message." });
    if (n.type === "sftp" && !n.connection) issues.push({ node: n.id, level: "error", code: "missing_connection", message: "Choisissez la connexion (type « Serveur SFTP »)." });
    if (n.type === "ldap" && !n.connection) issues.push({ node: n.id, level: "error", code: "missing_connection", message: "Choisissez la connexion (type « Annuaire LDAP »)." });
    if (n.type === "sql" && !n.connection) issues.push({ node: n.id, level: "error", code: "missing_connection", message: "Choisissez la connexion (type « Base PostgreSQL »)." });
    if (n.type === "http" && !n.connection) issues.push({ node: n.id, level: "error", code: "missing_connection", message: "Choisissez la connexion (type « Service HTTP ») utilisée par la requête." });
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
    case "switch": return `${(n.cases ?? []).length} cas + sinon`;
    case "script": return `${n.language === "javascript" ? "JavaScript" : "Python"} · ${(n.script ?? "").split("\n").find((l) => l.trim() && !l.trim().startsWith("#")) ?? "script vide"}`.slice(0, 70);
    case "throw": return n.message ? `Lève : ${n.message}` : "Exception à définir";
    case "agent": return `${n.mode === "reel" ? "" : "[test] "}Agent · ${(n.tools ?? []).length} outil(s)`;
    case "smtp": return `${n.mode === "reel" ? "" : "[test] "}E-mail à ${n.to || "?"} : ${n.subject || "objet à saisir"}`.slice(0, 70);
    case "sftp": return `${n.mode === "reel" ? "" : "[test] "}SFTP ${n.operation ?? "list"} ${n.path ?? ""}`.slice(0, 70);
    case "ldap": return `${n.mode === "reel" ? "" : "[test] "}Annuaire ${n.filter ?? ""}`.slice(0, 70);
    case "sql": return `${n.mode === "reel" ? "" : "[test] "}${(n.statement ?? "").replace(/\s+/g, " ").slice(0, 50) || "requête à saisir"}`;
    case "http": if (n.integration) return `${n.mode === "reel" ? "" : "[test] "}${n.integration} · ${n.operation ?? "?"}`; return `${n.mode === "reel" ? "" : "[test] "}${n.method ?? "GET"} ${n.connection || "?"}${n.path ?? ""}`.slice(0, 70);
    case "transform": return `${({ filter: "Filtrer", map: "Transformer", sort: "Trier", group: "Regrouper", unique: "Dédoublonner", merge: "Fusionner", limit: "Limiter", set: "Définir", split: "Scinder", chunk: "Lots" } as Record<string, string>)[n.op ?? ""] ?? "Transformer"} ${n.items ?? ""}`.trim().slice(0, 70);
    case "wait": return n.resume === "webhook" ? `Attendre un appel HTTP (max ${n.seconds ?? "?"} s)` : `Attendre ${n.seconds ?? "?"} s`;
    case "approval": return n.title ? `Demander : ${n.title}` : "Approbation à définir";
    case "foreach": return `Pour chaque élément (max ${n.max_iterations ?? "?"})`;
    case "ai": return n.skill ? `Skill IA : ${n.skill} (${n.provider || "fournisseur par défaut"})` : n.prompt ? `IA (${n.provider || "fournisseur ?"}) : ${n.prompt.slice(0, 40)}` : "Étape IA à définir";
    case "while": return `Répéter ${n.workflow || "?"} tant que ${(n.condition ?? "").slice(0, 30) || "?"} (max ${n.max_iterations ?? "?"})`;
    case "subworkflow": return `Appelle ${n.workflow ?? "?"}`;
    default: return "Attend tous les flux entrants";
  }
}
