import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { OutputsTab, usageExample } from "./OutputsTab";
import type { Graph, GraphNode } from "./types";

let current: unknown;
let childWorkflow: unknown;
vi.mock("./DraftContext", () => ({ useDraft: () => current }));
vi.mock("../lib", () => ({ useWsQuery: () => ({ data: childWorkflow }) }));

const ssh: GraphNode = { id: "s", type: "ssh", name: "Commandes", connection: "c", mode: "test" };
const sql: GraphNode = { id: "q", type: "sql", name: "Requête", connection: "c" };
const wait: GraphNode = { id: "w", type: "wait", name: "Attente", seconds: 5 };
const sub: GraphNode = { id: "sw", type: "subworkflow", name: "Appel", workflow: "enfant" };

function Harness({ node = ssh, outputs = {}, editable = true }: { node?: GraphNode; outputs?: Record<string, string>; editable?: boolean }) {
  const [graph, setGraph] = useState<Graph>({ inputs: { type: "object", properties: {} }, nodes: [node], edges: [], outputs } as unknown as Graph);
  current = { graph, setGraph: (g: Graph | ((p: Graph) => Graph)) => setGraph(g), actions: [], editable };
  return (
    <>
      <OutputsTab node={node} />
      <output aria-label="sorties du workflow">{JSON.stringify(graph.outputs)}</output>
    </>
  );
}
const wfOutputs = () => JSON.parse(screen.getByLabelText("sorties du workflow").textContent ?? "{}") as Record<string, string>;
const list = () => screen.getByRole("list", { name: /Variables de sortie de l'étape/ });

describe("Onglet Sorties de l'étape", () => {
  it("tells in one line how to use the outputs, with a real example, the rest folded away", () => {
    render(<Harness />);
    expect(screen.getByText(/Reliez cette étape/)).toBeInTheDocument();
    expect(screen.getByText(/Reliez cette étape/).textContent).toContain("{{ steps.s.output.status }}");
    const more = screen.getByText("Plus d'aide").closest("details") as HTMLDetailsElement;
    expect(more.open).toBe(false);
    expect(within(more).getByText(/constantes/)).toBeInTheDocument();
  });

  it("shows one line per variable: name, type and the expression to copy (a list gets [0], an object a key)", async () => {
    render(<Harness node={sql} />);
    expect(within(list()).getAllByRole("listitem")).toHaveLength(5); // rows, count, columns, truncated, simulated: all visible, nothing to expand
    expect(within(list()).getAllByRole("listitem")[0]).toHaveTextContent("rows"); // the useful ones come first
    expect(within(list()).getByText("{{ steps.q.output.rows[0] }}")).toBeInTheDocument();
    expect(within(list()).getByText("{{ steps.q.output.count }}")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Afficher les/ })).toBeNull();
    expect(usageExample({ path: "steps.a.output.o", label: "o", type: "object" })).toBe("{{ steps.a.output.o.nom_du_champ }}");
  });

  it("shows the first variables and folds the others behind one button", async () => {
    render(<Harness />);
    expect(within(list()).getAllByRole("listitem")).toHaveLength(6);
    expect(within(list()).queryByText("{{ steps.s.output.error.nom_du_champ }}")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Afficher les \d+ autres variables/ }));
    expect(within(list()).getAllByRole("listitem").length).toBeGreaterThan(6);
    expect(within(list()).getByText("{{ steps.s.output.error.nom_du_champ }}")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Afficher moins" }));
    expect(within(list()).getAllByRole("listitem")).toHaveLength(6);
  });

  it("copies the expression and confirms on the button", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "Copier l'expression de status" }));
    expect(writeText).toHaveBeenCalledWith("{{ steps.s.output.status }}");
    expect(await screen.findByRole("button", { name: "Copier l'expression de status" })).toHaveTextContent("Copié");
  });

  it("keeps the link to a workflow output folded until asked for, then creates one and shows it as linked", async () => {
    render(<Harness />);
    expect(screen.queryByLabelText(/Nouvelle sortie du workflow/)).toBeNull();
    const toggle = screen.getByRole("button", { name: "Lier status à une sortie du workflow" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(screen.getByRole("button", { name: "Créer la sortie s_status liée à status" }));
    expect(wfOutputs()).toEqual({ s_status: "{{ steps.s.output.status }}" });
    expect(screen.getByText("↦ s_status")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lier status à une sortie du workflow" })).toHaveTextContent("Modifier le lien");
  });

  it("links a variable to a predefined workflow output that is still empty, and offers no output already used", async () => {
    render(<Harness outputs={{ resultat: "", deja_pris: "{{ inputs.x }}" }} />);
    await userEvent.click(screen.getByRole("button", { name: "Lier status à une sortie du workflow" }));
    const select = screen.getByLabelText("Sortie du workflow existante (encore vide)");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Choisir…", "resultat"]);
    await userEvent.selectOptions(select, "resultat");
    expect(wfOutputs()).toEqual({ resultat: "{{ steps.s.output.status }}", deja_pris: "{{ inputs.x }}" });
  });

  it("never overwrites an existing output by creating one of the same name", async () => {
    render(<Harness outputs={{ s_status: "{{ inputs.x }}" }} />);
    await userEvent.click(screen.getByRole("button", { name: "Lier status à une sortie du workflow" }));
    expect(screen.getByRole("button", { name: "Créer la sortie s_status liée à status" })).toBeDisabled();
    expect(screen.getByText(/existe déjà/)).toBeInTheDocument();
  });

  it("unlinks by removing that output from the workflow", async () => {
    render(<Harness outputs={{ s_status: "{{ steps.s.output.status }}", autre: "{{ inputs.x }}" }} />);
    await userEvent.click(screen.getByRole("button", { name: "Lier status à une sortie du workflow" }));
    await userEvent.click(screen.getByRole("button", { name: "Retirer la sortie s_status du workflow" }));
    expect(wfOutputs()).toEqual({ autre: "{{ inputs.x }}" });
  });

  it("offers no link control when the workflow cannot be edited, but still shows the usage", () => {
    render(<Harness editable={false} />);
    expect(screen.queryByRole("button", { name: /Lier .* à une sortie du workflow/ })).toBeNull();
    expect(within(list()).getByText("{{ steps.s.output.status }}")).toBeInTheDocument();
  });

  it("says plainly when a step has no output variable", () => {
    render(<Harness node={wait} />);
    expect(screen.getByText(/minuterie n.a pas de variable de sortie/)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: /Variables de sortie/ })).toBeNull();
  });

  it.each([
    ["http", { id: "h", type: "http", name: "Appel", connection: "c" }, ["status", "ok", "body", "headers", "file", "simulated"]],
    ["approval", { id: "a", type: "approval", name: "Validation", title: "t" }, ["approved", "decided_by", "comment"]],
    ["foreach", { id: "f", type: "foreach", name: "Boucle" }, ["results", "count"]],
    ["race", { id: "r", type: "race", name: "Course" }, ["winner", "winner_name", "value"]],
    ["smtp", { id: "m", type: "smtp", name: "Mail", connection: "c" }, ["sent", "recipients", "message_id", "simulated"]],
    ["sftp", { id: "t", type: "sftp", name: "Fichier", connection: "c" }, ["content", "entries", "operation", "path", "count", "file", "simulated"]],
    ["ldap", { id: "l", type: "ldap", name: "Annuaire", connection: "c" }, ["rows", "count", "truncated", "simulated"]],
    ["agent", { id: "g", type: "agent", name: "Agent" }, ["answer", "iterations", "steps", "stopped", "simulated"]],
    ["wait (reprise par appel)", { id: "w", type: "wait", name: "Attente", resume: "webhook" }, ["resumed", "payload"]],
  ] as [string, GraphNode, string[]][])("lists the guaranteed outputs of a %s step, as the validator accepts them", (_name, node, keys) => {
    render(<Harness node={node} />);
    const shown = within(list()).getAllByRole("listitem").map((li) => li.textContent ?? "");
    for (const k of keys.slice(0, 6)) expect(shown.join(" ")).toContain(`steps.${node.id}.output.${k}`);
  });

  it("reads the outputs of a sub-workflow step from the workflow it calls", () => {
    childWorkflow = { draft_graph: { outputs: { total: "{{ steps.x.output.t }}", statut: "{{ inputs.s }}" } } };
    render(<Harness node={sub} />);
    expect(within(list()).getByText("{{ steps.sw.output.total }}")).toBeInTheDocument();
    expect(within(list()).getByText("{{ steps.sw.output.statut }}")).toBeInTheDocument();
    expect(screen.getByText(/sorties du workflow appelé/)).toBeInTheDocument();
  });

  it("explains an empty sub-workflow step: no workflow chosen yet, or none declared", () => {
    childWorkflow = undefined;
    const { unmount } = render(<Harness node={{ ...sub, workflow: "" }} />);
    expect(screen.getByText(/Choisissez d'abord le workflow à appeler/)).toBeInTheDocument();
    unmount();
    childWorkflow = { draft_graph: { outputs: {} } };
    render(<Harness node={sub} />);
    expect(screen.getByText(/ne déclare aucune sortie/)).toBeInTheDocument();
  });

  it("explains why a timer and an exception have no output", () => {
    const { unmount } = render(<Harness node={wait} />);
    expect(screen.getByText(/minuterie n'a pas de variable de sortie/)).toBeInTheDocument();
    unmount();
    render(<Harness node={{ id: "x", type: "throw", name: "Erreur", code: "E", message: "m" }} />);
    expect(screen.getByText(/exception n'a pas de variable de sortie/)).toBeInTheDocument();
  });
});
