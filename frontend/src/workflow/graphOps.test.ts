import type { Action } from "../api";
import { addNode, allVariables, ancestors, autoLayout, availableVariables, bendEdge, moveEndpoint, relabel, clientValidate, connect, defaultLabel, defaultNode, duplicateNode, emptyGraph, placeNewNodes, removeNode, topoOrder, typeCompatible, uniqueId, wouldCreateCycle } from "./graphOps";

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

  it("removes a node with its edges and duplicates without edges", () => {
    const g = removeNode(chain(), "echo_2");
    expect(g.nodes).toHaveLength(2);
    expect(g.edges).toHaveLength(0);
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

  it("remembers the hand-placed endpoints and bent lines, and a fresh layout forgets them", () => {
    let g = chain();
    const e = g.edges[0] as { from: string; to: string };
    g = bendEdge(moveEndpoint(moveEndpoint(g, "start", -50.4, 12), "end", 900, 3), e.from, e.to, 123.6);
    expect(g.layout).toEqual({ start: { x: -50, y: 12 }, end: { x: 900, y: 3 } });
    expect(g.edges[0]?.ui).toEqual({ cx: 124 });
    expect(relabel(g, e.from, e.to, "error").edges[0]).toEqual({ from: e.from, to: e.to, label: "error", ui: { cx: 124 } });
    expect(bendEdge(g, e.from, e.to, null).edges[0]?.ui).toBeUndefined();
    const fresh = autoLayout(g);
    expect(fresh.layout).toBeUndefined();
    expect(fresh.edges.every((x) => x.ui === undefined)).toBe(true);
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
    const before = g.nodes[0]!.ui;
    const proposed = { ...g, nodes: [...g.nodes, { id: "b", type: "wait" as const, name: "B" }, { id: "c", type: "wait" as const, name: "C" }], edges: [{ from: "a", to: "b" }, { from: "a", to: "c" }] };
    const out = placeNewNodes(proposed);
    expect(out.nodes[0]!.ui).toEqual(before);
    const [b, c] = [out.nodes[1]!.ui!, out.nodes[2]!.ui!];
    expect(b.x).toBe(before!.x + 200);
    expect(`${c.x}:${c.y}`).not.toBe(`${b.x}:${b.y}`); // two branches do not overlap
  });

  it("places notes proposed without a position above the schema, side by side", () => {
    let g = emptyGraph();
    g = addNode(g, { ...defaultNode(g, "wait"), id: "a" });
    const out = placeNewNodes({ ...g, notes: [{ id: "note_1", text: "a", ui: { x: 5, y: 5 } }, { id: "note_2", text: "b" } as never, { id: "note_3", text: "c" } as never] });
    expect(out.notes![0]!.ui).toEqual({ x: 5, y: 5 }); // existing note untouched
    const [n2, n3] = [out.notes![1]!.ui!, out.notes![2]!.ui!];
    expect(n2.y).toBeLessThan(g.nodes[0]!.ui!.y);
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
