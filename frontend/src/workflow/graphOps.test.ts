import type { Graph, GraphNode } from "./types";

/** Valeur attendue présente : échoue avec un message clair plutôt que par une erreur de type à l'exécution. */
function must<T>(v: T | null | undefined): T {
  if (v === null || v === undefined) throw new Error("valeur attendue absente");
  return v;
}
import type { Action } from "../api";
import { BAND_LAYERS, GRID_X, GRID_Y, layeredPositions, addNode, allVariables, ancestors, autoLayout, availableVariables, setEdgePoints, moveEndpoint, relabel, clientValidate, connect, defaultLabel, defaultNode, duplicateNode, emptyGraph, placeNewNodes, removeNode, summarize, topoOrder, typeCompatible, uniqueId, wouldCreateCycle } from "./graphOps";

const echo: Action = {
  slug: "echo", name: "Écho", description: "", domain: "", folder: "", tags: [], owner: null, status: "active",
  latest_version: {
    version: "1.0.0", license: "Apache-2.0", side_effects: "none", idempotent: true, examples: [],
    input_schema: { type: "object", required: ["value"], properties: { value: { type: "string", title: "Valeur" } } },
    output_schema: { type: "object", properties: { value: { type: "string", title: "Valeur" } } },
  },
};

function chain() {
  let g = emptyGraph();
  g = addNode(g, defaultNode(g, "action", echo));
  g = addNode(g, defaultNode(g, "action", echo), "echo");
  g = addNode(g, defaultNode(g, "condition"), "echo_2");
  return g;
}

describe("graph operations", () => {
  it("creates unique ids and connects after the selected node", () => {
    const g = chain();
    expect(g.nodes.map((n) => n.id)).toEqual(["echo", "echo_2", "condition"]);
    expect(g.edges).toEqual([{ from: "echo", to: "echo_2" }, { from: "echo_2", to: "condition" }]);
    expect(uniqueId(g, "Écho 3!")).toBe("echo_3");
    expect(uniqueId(g, "Écho 2")).toBe("echo_2_2");
  });

  it("labels condition branches true then false", () => {
    let g = chain();
    g = addNode(g, defaultNode(g, "action", echo), "condition");
    g = addNode(g, defaultNode(g, "action", echo), "condition");
    expect(g.edges.filter((e) => e.from === "condition").map((e) => e.label)).toEqual(["true", "false"]);
  });

  it("rejects cycles and detects them", () => {
    const g = chain();
    expect(wouldCreateCycle(g, "condition", "echo")).toBe(true);
    expect(wouldCreateCycle(g, "echo", "condition")).toBe(false);
    const cyclic = connect(g, "condition", "echo");
    expect(topoOrder(cyclic)).toBeNull();
    expect(clientValidate(cyclic, [echo]).some((i) => i.code === "cycle")).toBe(true);
  });

  it("removes a node (its neighbours are linked together) and duplicates without edges", () => {
    const g = removeNode(chain(), "echo_2");
    expect(g.nodes).toHaveLength(2);
    expect(g.edges).toEqual([{ from: "echo", to: "condition" }]); // le milieu d'une ligne : ses voisins sont reliés entre eux
    const dup = duplicateNode(chain(), "echo");
    expect(dup?.graph.nodes).toHaveLength(4);
    expect(dup?.graph.edges).toHaveLength(2);
  });

  it("computes upstream variables only", () => {
    const g = chain();
    expect(ancestors(g, "condition")).toEqual(new Set(["echo", "echo_2"]));
    const vars = availableVariables(g, "echo_2", [echo]).map((v) => v.path);
    expect(vars).toEqual(["steps.echo.output.value"]);
    expect(availableVariables(g, "echo", [echo])).toEqual([]);
  });

  it("flags missing required parameters and implicit joins", () => {
    let g = chain();
    expect(clientValidate(g, [echo]).filter((i) => i.code === "missing_param")).toHaveLength(2);
    g = connect(g, "echo", "condition");
    expect(clientValidate(g, [echo]).some((i) => i.code === "implicit_join" && i.node === "condition")).toBe(true);
  });

  it("lays out left to right by levels", () => {
    const g = autoLayout(chain());
    expect(g.nodes.map((n) => n.ui?.x)).toEqual([0, 200, 400]);
    expect(g.nodes.map((n) => n.ui?.y)).toEqual([0, 0, 0]);
  });

  it("remembers the hand-placed endpoints and the points of a line, and a fresh layout forgets them", () => {
    let g = chain();
    const e = g.edges[0] as { from: string; to: string };
    g = setEdgePoints(moveEndpoint(moveEndpoint(g, "start", -50.4, 12), "end", 900, 3), e.from, e.to, [{ x: 100.4, y: 80.6 }, { x: 150, y: -40 }]);
    expect(g.layout).toEqual({ start: { x: -50, y: 12 }, end: { x: 900, y: 3 } });
    expect(g.edges[0]?.ui).toEqual({ points: [{ x: 100, y: 81 }, { x: 150, y: -40 }] });
    expect(relabel(g, e.from, e.to, "error").edges[0]).toEqual({ from: e.from, to: e.to, label: "error", ui: { points: [{ x: 100, y: 81 }, { x: 150, y: -40 }] } });
    expect(g.edges.slice(1).every((x) => x.ui === undefined)).toBe(true); // only the chosen line changes
    expect(setEdgePoints(g, e.from, e.to, null).edges[0]?.ui).toBeUndefined();
    expect(setEdgePoints(g, e.from, e.to, []).edges[0]?.ui).toBeUndefined();
    const fresh = autoLayout(g);
    expect(fresh.layout).toBeUndefined();
    expect(fresh.edges.every((x) => x.ui === undefined)).toBe(true);
  });

  it("replaces the older horizontal setting (cx) once points are placed, and keeps it until then", () => {
    const g = chain();
    const e = g.edges[0] as { from: string; to: string };
    const old = { ...g, edges: g.edges.map((x, i) => (i === 0 ? { ...x, ui: { cx: 120 } } : x)) };
    expect(relabel(old, e.from, e.to, "error").edges[0]?.ui).toEqual({ cx: 120 });
    expect(setEdgePoints(old, e.from, e.to, [{ x: 10, y: 10 }]).edges[0]?.ui).toEqual({ points: [{ x: 10, y: 10 }] });
  });

  it("checks type compatibility (integer fits number)", () => {
    expect(typeCompatible("integer", "number")).toBe(true);
    expect(typeCompatible("string", "integer")).toBe(false);
    expect(typeCompatible(undefined, "integer")).toBe(true);
  });
});

describe("script tasks, variables and horizontal layout", () => {
  it("creates a script task with sensible defaults", () => {
    const g = emptyGraph();
    const n = defaultNode(g, "script");
    expect(n).toMatchObject({ type: "script", language: "python", timeout_seconds: 60, bindings: {}, outputs: [{ name: "resultat", type: "string" }] });
    expect(n.script).toContain("System.log");
  });

  it("exposes workflow variables and typed script outputs to downstream steps", () => {
    let g = emptyGraph();
    g = { ...g, variables: [{ name: "prefixe", type: "string", value: "VM" }] };
    const s = { ...defaultNode(g, "script"), outputs: [{ name: "total", type: "integer" as const }] };
    g = addNode(g, s);
    g = addNode(g, defaultNode(g, "action", echo), s.id);
    const vars = availableVariables(g, "echo", [echo]);
    expect(vars.map((v) => v.path)).toEqual(expect.arrayContaining(["vars.prefixe", `steps.${s.id}.output.total`]));
    expect(vars.find((v) => v.path.endsWith(".total"))?.type).toBe("integer");
    expect(availableVariables(g, s.id, [echo]).some((v) => v.path.endsWith(".total"))).toBe(false); // not before it runs
    expect(allVariables(g, [echo]).some((v) => v.path === `steps.${s.id}.output.total`)).toBe(true);
  });

  it("positions the steps proposed by the assistant right of their predecessor without moving anything else", () => {
    let g = emptyGraph();
    g = addNode(g, { ...defaultNode(g, "wait"), id: "a" });
    const before = must(g.nodes[0]).ui;
    const proposed = { ...g, nodes: [...g.nodes, { id: "b", type: "wait" as const, name: "B" }, { id: "c", type: "wait" as const, name: "C" }], edges: [{ from: "a", to: "b" }, { from: "a", to: "c" }] };
    const out = placeNewNodes(proposed);
    expect(must(out.nodes[0]).ui).toEqual(before);
    const [b, c] = [must(must(out.nodes[1]).ui), must(must(out.nodes[2]).ui)];
    expect(b.x).toBe(must(before).x + 200);
    expect(`${c.x}:${c.y}`).not.toBe(`${b.x}:${b.y}`); // two branches do not overlap
  });

  it("places notes proposed without a position above the schema, side by side", () => {
    let g = emptyGraph();
    g = addNode(g, { ...defaultNode(g, "wait"), id: "a" });
    const out = placeNewNodes({ ...g, notes: [{ id: "note_1", text: "a", ui: { x: 5, y: 5 } }, { id: "note_2", text: "b" } as never, { id: "note_3", text: "c" } as never] });
    expect(must(must(out.notes)[0]).ui).toEqual({ x: 5, y: 5 }); // existing note untouched
    const notes = must(out.notes);
    const [n2, n3] = [must(must(notes[1]).ui), must(must(notes[2]).ui)];
    expect(n2.y).toBeLessThan(must(must(g.nodes[0]).ui).y);
    expect(n3.x).toBeGreaterThan(n2.x);
  });

  it("blocks a script written by the AI until a person has confirmed reading it", () => {
    let g = emptyGraph();
    g = addNode(g, { ...defaultNode(g, "script"), ai_review: true });
    expect(clientValidate(g, [echo]).some((i) => i.code === "ai_review_required")).toBe(true);
    g = { ...g, nodes: g.nodes.map((n) => ({ ...n, ai_review: false })) };
    expect(clientValidate(g, [echo]).some((i) => i.code === "ai_review_required")).toBe(false);
  });

  it("flags an empty script and places new nodes to the right without overlapping", () => {
    let g = emptyGraph();
    g = addNode(g, { ...defaultNode(g, "script"), script: "  " });
    expect(clientValidate(g, [echo]).some((i) => i.code === "empty_script")).toBe(true);
    g = addNode(g, defaultNode(g, "wait"), g.nodes[0]?.id);
    g = addNode(g, defaultNode(g, "wait"), g.nodes[0]?.id); // same anchor: must not land on the same slot
    const slots = g.nodes.map((n) => `${n.ui?.x}:${n.ui?.y}`);
    expect(new Set(slots).size).toBe(slots.length);
    expect(g.nodes[1]?.ui?.x).toBe(200);
  });
});

describe("AI step using a library skill", () => {
  const ai = (extra: object) => ({ ...emptyGraph(), nodes: [{ id: "i", name: "IA", type: "ai" as const, ui: { x: 0, y: 0 }, ...extra }] });
  const codes = (g: ReturnType<typeof ai>) => clientValidate(g, []).map((i) => i.code);

  it("needs neither a free-form task nor a node provider (the skill brings them)", () => {
    expect(codes(ai({ skill: "qualifier", inputs: {} }))).not.toContain("missing_prompt");
    expect(codes(ai({ skill: "qualifier", inputs: {} }))).not.toContain("unknown_provider");
  });

  it("still asks to pick a skill once the mode is chosen", () => {
    expect(codes(ai({ skill: "" }))).toContain("missing_skill");
  });

  it("keeps validating free-form AI steps as before", () => {
    expect(codes(ai({ prompt: "", provider: "" }))).toEqual(expect.arrayContaining(["missing_prompt", "unknown_provider"]));
  });
});

describe("choix multiple (switch)", () => {
  it("propose les cas dans l'ordre puis « default », et signale un cas sans condition", () => {
    let g = emptyGraph();
    const sw = { ...defaultNode(g, "switch"), id: "choix", cases: [{ id: "prod", expr: "inputs.env == 'prod'" }, { id: "dev", expr: "" }] };
    g = addNode(g, sw);
    expect(defaultLabel(g, sw)).toBe("prod");
    g = addNode(g, { ...defaultNode(g, "wait"), id: "a" }, "choix");
    expect(g.edges).toEqual([{ from: "choix", to: "a", label: "prod" }]);
    g = addNode(g, { ...defaultNode(g, "wait"), id: "b" }, "choix");
    g = addNode(g, { ...defaultNode(g, "wait"), id: "c" }, "choix");
    expect(g.edges.map((e) => e.label)).toEqual(["prod", "dev", "default"]);
    expect(clientValidate(g, []).some((i) => i.code === "missing_expr" && i.node === "choix")).toBe(true);
  });
});

describe("requête HTTP", () => {
  it("démarre en mode test et expose code, succès et réponse aux étapes suivantes", () => {
    let g = emptyGraph();
    const h = { ...defaultNode(g, "http"), id: "appel", connection: "svc" };
    expect(h.mode).toBe("test");
    g = addNode(g, h);
    const next = { ...defaultNode(g, "wait"), id: "apres" };
    g = addNode(g, next, "appel");
    const paths = availableVariables(g, "apres", []).map((v) => v.path);
    expect(paths).toEqual(expect.arrayContaining(["steps.appel.output.status", "steps.appel.output.ok", "steps.appel.output.body"]));
  });
});

describe("layered auto-layout", () => {
  const n = (id: string): GraphNode => ({ id, type: "script", name: id });
  const g = (ids: string[], edges: [string, string][]) => ({ ...emptyGraph(), nodes: ids.map(n), edges: edges.map(([from, to]) => ({ from, to })) });

  it("centres a fan-out on its source and a join on its branches", () => {
    const pos = layeredPositions(g(["a", "b", "c", "d", "e"], [["a", "b"], ["a", "c"], ["a", "d"], ["b", "e"], ["c", "e"], ["d", "e"]]));
    expect(pos.get("c")?.y).toBe(pos.get("a")?.y);
    expect(pos.get("e")?.y).toBe(pos.get("c")?.y);
    expect(pos.get("b")?.y).toBe((pos.get("c")?.y ?? 0) - GRID_Y);
  });

  it("never overlaps two nodes", () => {
    const ids = Array.from({ length: 60 }, (_, i) => `n${i}`);
    const edges: [string, string][] = ids.slice(1).map((id, i) => [`n${Math.floor(i / 2)}`, id]);
    const seen = new Set<string>();
    for (const p of layeredPositions(g(ids, edges)).values()) { const k = `${p.x}:${p.y}`; expect(seen.has(k)).toBe(false); seen.add(k); }
  });

  it("untangles crossing branches", () => {
    // declared a1,a2 / b1,b2 but wired a1->b2 and a2->b1: the order must follow the wiring
    const pos = layeredPositions(g(["r", "a1", "a2", "b1", "b2"], [["r", "a1"], ["r", "a2"], ["a1", "b2"], ["a2", "b1"]]));
    const above = (p: string, q: string) => (pos.get(p)?.y ?? 0) < (pos.get(q)?.y ?? 0);
    expect(above("a1", "a2")).toBe(above("b2", "b1"));
  });

  it("folds a long chain onto stacked bands", () => {
    const ids = Array.from({ length: BAND_LAYERS * 2 + 3 }, (_, i) => `s${i}`);
    const pos = layeredPositions(g(ids, ids.slice(1).map((id, i) => [`s${i}`, id])));
    const xs = [...pos.values()].map((p) => p.x);
    expect(Math.max(...xs)).toBe((BAND_LAYERS - 1) * GRID_X);
    expect(pos.get(`s${BAND_LAYERS}`)?.x).toBe(0);
    expect(pos.get(`s${BAND_LAYERS}`)?.y).toBeGreaterThan(pos.get("s0")?.y ?? 0);
  });

  it("lays out a workflow that has no position at all, and leaves a partly placed one alone", () => {
    const base = g(["a", "b", "c"], [["a", "b"], ["a", "c"]]);
    const out = placeNewNodes(base);
    expect(out.nodes.every((x) => x.ui)).toBe(true);
    expect(out.nodes.find((x) => x.id === "b")?.ui?.x).toBe(GRID_X);
    const partly = { ...base, nodes: base.nodes.map((x) => (x.id === "a" ? { ...x, ui: { x: 7, y: 9 } } : x)) };
    expect(placeNewNodes(partly).nodes.find((x) => x.id === "a")?.ui).toEqual({ x: 7, y: 9 });
  });
});

describe("SSH step", () => {
  const ssh = (over: Partial<GraphNode> = {}): GraphNode => ({ ...defaultNode(emptyGraph(), "ssh"), ...over });

  it("starts in test mode, direct, with one empty command and stop on error", () => {
    const n = ssh();
    expect(n).toMatchObject({ type: "ssh", mode: "test", session: "exec", jumps: [], stop_on_error: true, commands: [{ run: "" }] });
  });

  it("asks for a connection and for something to run, in each session kind", () => {
    const msgs = (n: GraphNode) => clientValidate({ ...emptyGraph(), nodes: [n] }, []).map((i) => i.code);
    expect(msgs(ssh())).toEqual(expect.arrayContaining(["missing_connection", "bad_ssh"]));
    expect(msgs(ssh({ connection: "c", commands: [{ run: "uptime" }] }))).toEqual([]);
    expect(msgs(ssh({ connection: "c", commands: "{{ steps.a.output.l }}" }))).toEqual([]); // a dynamic list is checked by the server
    expect(msgs(ssh({ connection: "c", session: "shell", steps: [] }))).toContain("bad_ssh");
    expect(msgs(ssh({ connection: "c", jumps: [""], commands: [{ run: "x" }] }))).toContain("bad_ssh");
  });

  it("summarizes without exposing commands", () => {
    expect(summarize(ssh({ connection: "c", jumps: ["a", "b"], commands: [{ run: "secret-command" }] }), [])).toBe("[test] SSH via 2 rebond(s) 1 commande(s)");
    expect(summarize(ssh({ session: "shell", steps: [{}, {}], mode: "reel" }), [])).toBe("SSH session · 2 étape(s)");
  });

  it("exposes the structured outputs to the following steps", () => {
    const g = { ...emptyGraph(), nodes: [{ ...ssh({ connection: "c", id: "pose" }) }, { ...defaultNode(emptyGraph(), "action", echo), id: "suite" }], edges: [{ from: "pose", to: "suite", label: "next" }] };
    const paths = availableVariables(g, "suite", []).map((v) => v.path);
    expect(paths).toEqual(expect.arrayContaining(["steps.pose.output.results", "steps.pose.output.status", "steps.pose.output.failed_step", "steps.pose.output.uncertain"]));
  });
});

describe("output variables of every step type", () => {
  it("lists, for each type the validator knows, exactly the fields it guarantees (one source: staticOutputs.json)", async () => {
    const guaranteed = (await import("./staticOutputs.json")).default as Record<string, Record<string, string | null>>;
    for (const [type, fields] of Object.entries(guaranteed)) {
      if (type === "join" || type === "wait" || type === "wait_resume") continue;
      const node = { ...defaultNode(emptyGraph(), type as GraphNode["type"]), id: "x", name: "X" };
      const g = { ...emptyGraph(), nodes: [node, { ...defaultNode(emptyGraph(), "action", echo), id: "y" }], edges: [{ from: "x", to: "y", label: "next" }] };
      const paths = availableVariables(g, "y", [echo]).map((v) => v.path).filter((p) => p.startsWith("steps.x.output."));
      expect(paths.sort(), type).toEqual(Object.keys(fields).map((k) => `steps.x.output.${k}`).sort());
    }
  });

  it("gives a wait step outputs only when it resumes by webhook or form", () => {
    const base = { ...defaultNode(emptyGraph(), "wait"), id: "w", name: "W" };
    const at = (n: GraphNode) => availableVariables({ ...emptyGraph(), nodes: [n, { ...defaultNode(emptyGraph(), "action", echo), id: "y" }], edges: [{ from: "w", to: "y", label: "next" }] }, "y", [echo]).filter((v) => v.path.startsWith("steps.w.")).length;
    expect(at(base)).toBe(0);
    expect(at({ ...base, resume: "webhook" })).toBe(2);
  });
});

describe("insertion d'un élément dans une ligne", () => {
  const pairs = (g: Graph) => g.edges.map((e) => `${e.from}>${e.to}${e.label && e.label !== "next" ? `:${e.label}` : ""}`).sort();

  it("s'intercale entre l'étape choisie et sa suivante au lieu de créer une branche vers Fin", () => {
    let g = emptyGraph();
    g = addNode(g, defaultNode(g, "action", echo));
    g = addNode(g, defaultNode(g, "action", echo), "echo");
    expect(pairs(g)).toEqual(["echo>echo_2"]);
    g = addNode(g, defaultNode(g, "wait"), "echo");
    expect(pairs(g)).toEqual(["echo>wait", "wait>echo_2"]);
  });

  it("n'insère pas après une décision (la branche est à choisir), ni quand l'étape a plusieurs sorties", () => {
    let g = emptyGraph();
    g = addNode(g, defaultNode(g, "condition"));
    g = addNode(g, defaultNode(g, "action", echo), "condition");
    expect(pairs(g)).toEqual(["condition>echo:true"]);
    g = addNode(g, defaultNode(g, "wait"), "condition");
    expect(pairs(g)).toEqual(["condition>echo:true", "condition>wait:false"]);
    let f = emptyGraph();
    f = addNode(f, defaultNode(f, "action", echo));
    f = addNode(f, defaultNode(f, "wait"), "echo");
    f = connect(f, "echo", "wait_2");
    f = { ...f, nodes: [...f.nodes, { ...defaultNode(f, "wait"), id: "wait_2" }] };
    const before = pairs(f);
    f = addNode(f, defaultNode(f, "wait"), "echo");
    expect(pairs(f)).toEqual([...before, "echo>wait_3"].sort());
  });

  it("une étape sans suite reçoit simplement le nouvel élément, et un élément posé sans étape choisie ne crée aucun lien", () => {
    let g = emptyGraph();
    g = addNode(g, defaultNode(g, "action", echo));
    g = addNode(g, defaultNode(g, "wait"), "echo");
    expect(pairs(g)).toEqual(["echo>wait"]);
    g = addNode(g, defaultNode(g, "wait"));
    expect(pairs(g)).toEqual(["echo>wait"]);
  });
});

describe("suppression d'un élément dans une ligne", () => {
  const pairs = (g: Graph) => g.edges.map((e) => `${e.from}>${e.to}${e.label && e.label !== "next" ? `:${e.label}` : ""}`).sort();
  const line = () => {
    let g = emptyGraph();
    g = addNode(g, defaultNode(g, "action", echo));
    g = addNode(g, defaultNode(g, "action", echo), "echo");
    g = addNode(g, defaultNode(g, "wait"), "echo_2");
    return g;
  };

  it("relie l'élément qui précède à celui qui suit (A → X → B devient A → B)", () => {
    expect(pairs(line())).toEqual(["echo>echo_2", "echo_2>wait"]);
    expect(pairs(removeNode(line(), "echo_2"))).toEqual(["echo>wait"]);
  });

  it("garde la branche d'une décision, et supprimer un bout de ligne ne crée aucun lien", () => {
    let g = emptyGraph();
    g = addNode(g, defaultNode(g, "condition"));
    g = addNode(g, defaultNode(g, "action", echo), "condition");
    g = addNode(g, defaultNode(g, "wait"), "echo");
    expect(pairs(removeNode(g, "echo"))).toEqual(["condition>wait:true"]);
    expect(pairs(removeNode(line(), "wait"))).toEqual(["echo>echo_2"]);
    expect(pairs(removeNode(line(), "echo"))).toEqual(["echo_2>wait"]);
  });

  it("ne devine rien quand l'élément supprimé a plusieurs sorties", () => {
    let g = line();
    g = { ...g, nodes: [...g.nodes, { ...defaultNode(g, "wait"), id: "autre" }] };
    g = connect(g, "echo_2", "autre");
    expect(pairs(removeNode(g, "echo_2"))).toEqual([]);
  });
});
