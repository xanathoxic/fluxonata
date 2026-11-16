import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NodePanel } from "./NodePanel";
import type { Graph, GraphNode } from "./types";

vi.mock("../lib", () => ({ useWsQuery: () => ({ data: undefined, isError: false }), useWsMutation: () => ({ mutate: vi.fn() }) }));
vi.mock("../context", () => ({ useApp: () => ({ workspace: { slug: "ws" }, can: () => true }) }));

function panel(node: Partial<GraphNode> = {}) {
  const full = { id: "ap", type: "approval", name: "Accord", title: "Valider", ...node } as GraphNode;
  const graph = { inputs: { type: "object", properties: {} }, nodes: [full], edges: [] } as unknown as Graph;
  const onUpdate = vi.fn();
  render(
    <NodePanel
      graph={graph}
      node={full}
      actions={[]}
      workflows={[]}
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
const open = () => userEvent.click(screen.getByRole("tab", { name: "Interaction" }));

describe("Étape d'approbation : définition de la demande", () => {
  it("propose le titre, l'objet de la demande et un corps au format Markdown", async () => {
    panel({ subject: "Achat", body: "# Détail" });
    await open();
    expect(screen.getByLabelText("Ce qui doit être approuvé")).toHaveValue("Valider");
    expect(screen.getByLabelText("Objet de la demande")).toHaveValue("Achat");
    expect(screen.getByLabelText("Corps de la demande (Markdown)")).toHaveValue("# Détail");
    expect(screen.getByText(/Mise en page en Markdown/)).toBeInTheDocument();
  });

  it("enregistre l'objet tel qu'il est saisi", async () => {
    const onUpdate = panel();
    await open();
    await userEvent.type(screen.getByLabelText("Objet de la demande"), "A");
    expect(onUpdate).toHaveBeenCalledWith({ subject: "A" });
  });

  it("enregistre le corps et vide l'ancienne consigne pour ne pas la dupliquer", async () => {
    const onUpdate = panel();
    await open();
    await userEvent.type(screen.getByLabelText("Corps de la demande (Markdown)"), "x");
    expect(onUpdate).toHaveBeenCalledWith({ body: "x", instructions: "" });
  });

  it("reprend une ancienne consigne comme corps de la demande", async () => {
    panel({ instructions: "Vérifiez le budget." });
    await open();
    expect(screen.getByLabelText("Corps de la demande (Markdown)")).toHaveValue("Vérifiez le budget.");
  });
});
