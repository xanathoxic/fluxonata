import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { WorkflowSummary } from "../api";
import { NodePanel } from "./NodePanel";
import { emptyGraph, updateNode } from "./graphOps";
import type { Graph, GraphNode } from "./types";

const schema = (name: string) => ({ type: "object", required: [name], properties: { [name]: { type: "string" } } });
const VERSIONS = [
  { version: "1.2.0", created_at: "2026-11-16T10:00:00Z", input_schema: schema("recent") },
  { version: "1.1.0", created_at: "2026-11-10T10:00:00Z", input_schema: schema("ancien") },
];
vi.mock("../lib", () => ({
  useWsQuery: (key: string) => ({ data: key === "workflow-versions:child" ? { results: VERSIONS } : undefined, isError: false, isLoading: false }),
  useWsMutation: () => ({ mutate: vi.fn() }),
}));
vi.mock("./ExprPreview", () => ({ ExprPreview: () => null })); // lit le brouillon du contexte : sans objet ici
vi.mock("../context", () => ({ useApp: () => ({ workspace: { slug: "ws" }, can: () => true }) }));

const CHILD = {
  slug: "child",
  name: "Collecter l'inventaire",
  latest_version: { version: "1.2.0", input_schema: schema("recent") },
} as unknown as WorkflowSummary;
const OTHER = { slug: "other", name: "Autre", latest_version: { version: "3.0.0", input_schema: schema("x") } } as unknown as WorkflowSummary;
const TAB = { subworkflow: "Workflow", foreach: "Boucle", while: "Boucle" } as const;

function panel(type: keyof typeof TAB, node: Partial<GraphNode> = {}) {
  const full = { id: "n", type, name: "Appel", workflow: "child", ...node } as GraphNode;
  const graph = { ...emptyGraph(), nodes: [full] } as Graph;
  const onUpdate = vi.fn();
  render(
    <NodePanel
      graph={graph}
      node={full}
      actions={[]}
      workflows={[CHILD, OTHER]}
      issues={[]}
      editable
      onUpdate={onUpdate}
      onConnect={vi.fn()}
      onDisconnect={vi.fn()}
      onRelabel={vi.fn()}
      onDuplicate={vi.fn()}
      onDelete={vi.fn()}
      onOpenScript={vi.fn()}
    />,
  );
  return onUpdate;
}
const open = (type: keyof typeof TAB) => userEvent.click(screen.getByRole("tab", { name: TAB[type] }));
const select = () => screen.getByLabelText("Version du workflow appelé") as HTMLSelectElement;

describe.each(["subworkflow", "foreach", "while"] as const)("version du workflow appelé — étape %s", (type) => {
  it("affiche la version ÉPINGLÉE du graphe, pas la dernière du catalogue", async () => {
    panel(type, { version: "1.1.0" });
    await open(type);
    expect(select().value).toBe("1.1.0");
    expect(screen.getByText(/appelle la version 1\.1\.0\. La version 1\.2\.0, plus récente, n'est pas utilisée/)).toBeInTheDocument();
    expect(screen.getByLabelText("Workflow appelé").textContent).not.toMatch(/1\.2\.0/); // le libellé du workflow n'annonce plus « (v1.2.0) »
  });

  it("sans version épinglée : le dit clairement et ne choisit rien à la place de la personne", async () => {
    const onUpdate = panel(type);
    await open(type);
    expect(select().value).toBe("");
    expect(screen.getByText(/Aucune version n'est épinglée/)).toBeInTheDocument();
    expect(onUpdate).not.toHaveBeenCalled(); // l'ouverture du panneau ne modifie jamais le graphe
  });

  it("« Toujours la dernière » s'écrit `latest`, s'affiche tel quel et n'est pas confondu avec une version figée", async () => {
    const onUpdate = panel(type, { version: "latest" });
    await open(type);
    expect(select().value).toBe("latest");
    expect(screen.getByText(/Chaque exécution appelle la dernière version publiée/)).toBeInTheDocument();
    expect(screen.queryByText(/plus récente, n'est pas utilisée/)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument(); // « latest » n'est pas une version introuvable
    await userEvent.selectOptions(select(), "1.1.0");
    expect(onUpdate).toHaveBeenLastCalledWith({ version: "1.1.0" });
    await userEvent.selectOptions(select(), "latest");
    expect(onUpdate).toHaveBeenLastCalledWith({ version: "latest" });
  });

  it("choisir une version l'écrit dans l'étape ; revenir à « dernière » retire la clé", async () => {
    const onUpdate = panel(type, { version: "1.1.0" });
    await open(type);
    await userEvent.selectOptions(select(), "1.2.0");
    expect(onUpdate).toHaveBeenLastCalledWith({ version: "1.2.0" });
    await userEvent.selectOptions(select(), "");
    expect(onUpdate).toHaveBeenLastCalledWith({ version: undefined });
  });

  it("changer de workflow efface la version épinglée de l'ancien (elle n'existe pas dans l'autre)", async () => {
    const onUpdate = panel(type, { version: "1.1.0" });
    await open(type);
    await userEvent.selectOptions(screen.getByLabelText("Workflow appelé"), "other");
    expect(onUpdate).toHaveBeenCalledWith({ workflow: "other", version: undefined });
  });

  it("une version épinglée absente du catalogue est montrée telle quelle, avec une alerte", async () => {
    panel(type, { version: "0.9.0" });
    await open(type);
    expect(select().value).toBe("0.9.0");
    expect(screen.getByRole("option", { name: /0\.9\.0 \(introuvable/ })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("La version 0.9.0 n'existe pas");
  });

  it("propose les entrées de la version épinglée, pas celles de la dernière", async () => {
    panel(type, { version: "1.1.0" });
    await open(type);
    expect(screen.getByText("ancien")).toBeInTheDocument();
    expect(screen.queryByText("recent")).not.toBeInTheDocument();
  });
});

describe("le graphe local est exactement ce qui sera enregistré", () => {
  it("updateNode retire la clé patchée à undefined (aucun `version: undefined` invisible)", () => {
    const g = { ...emptyGraph(), nodes: [{ id: "n", type: "subworkflow", name: "A", workflow: "child", version: "1.1.0" } as GraphNode] } as Graph;
    const next = updateNode(g, "n", { version: undefined });
    expect("version" in (next.nodes[0] as object)).toBe(false);
    expect(JSON.stringify(next)).toBe(JSON.stringify({ ...g, nodes: [{ id: "n", type: "subworkflow", name: "A", workflow: "child" }] }));
    expect(updateNode(g, "n", { version: "1.2.0" }).nodes[0]?.version).toBe("1.2.0");
  });
});
