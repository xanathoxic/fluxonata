import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expressionsRead, InputsTab } from "./InputsTab";
import type { Graph, GraphNode } from "./types";

let current: unknown;
vi.mock("./DraftContext", () => ({ useDraft: () => current }));

const up: GraphNode = { id: "a", type: "sql", name: "Requête", connection: "c" };
const me: GraphNode = { id: "m", type: "ssh", name: "Commandes", connection: "c", host: "{{ inputs.cible }}", commands: ["echo {{ steps.a.output.rows[0].id }}", "echo {{ steps.zz.output.x }}"] } as unknown as GraphNode;
const graph = (extra: Partial<Graph> = {}) =>
  ({ inputs: { type: "object", properties: { cible: { type: "string", title: "Cible" } } }, variables: [{ name: "env", type: "string" }], nodes: [up, me], edges: [{ from: "a", to: "m" }], ...extra }) as unknown as Graph;

describe("Onglet Entrées de l'étape", () => {
  it("reads every expression of a step once, whatever the nesting", () => {
    expect(expressionsRead(me)).toEqual(["inputs.cible", "steps.a.output.rows[0].id", "steps.zz.output.x"]);
    expect(expressionsRead({ id: "x", type: "wait", name: "w" } as GraphNode)).toEqual([]);
  });

  it("flags what is read, tells which variable it comes from, and warns about an unavailable one", () => {
    current = { graph: graph(), actions: [] };
    render(<InputsTab node={me} />);
    const read = within(screen.getByRole("list", { name: /Variables lues/ }));
    expect(read.getByText("{{ inputs.cible }}")).toBeInTheDocument();
    expect(read.getByText("Entrée « Cible »")).toBeInTheDocument();
    expect(read.getByText("Non disponible ici")).toBeInTheDocument();
  });

  it("lists the available variables by source with a copy button and folds the rest", async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    current = { graph: graph(), actions: [] };
    render(<InputsTab node={me} />);
    expect(screen.getByRole("list", { name: "Entrées du workflow" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Variables du workflow" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Copier l'expression de inputs.cible" }));
    expect(write).toHaveBeenCalledWith("{{ inputs.cible }}");
  });

  it("says so when nothing is available or read", () => {
    current = { graph: { inputs: { type: "object", properties: {} }, nodes: [me], edges: [] }, actions: [] };
    render(<InputsTab node={{ id: "m", type: "wait", name: "Attente" } as GraphNode} />);
    expect(screen.getByText(/ne lit encore aucune variable/)).toBeInTheDocument();
    expect(screen.getByText(/Aucune variable pour l'instant/)).toBeInTheDocument();
  });
});
