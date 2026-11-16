import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WorkflowPreview } from "./WorkflowPreview";
import type { Graph } from "./types";

const child = {
  name: "Enfant",
  draft_graph: {
    inputs: { type: "object", properties: {} },
    nodes: [
      { id: "s", type: "script", name: "Calcul", script: "print(1)", language: "python" },
      { id: "w", type: "subworkflow", name: "Petit-enfant", workflow: "petit" },
    ],
    edges: [],
  } as unknown as Graph,
};
vi.mock("../lib", () => ({ useWsQuery: () => ({ data: child, isError: false }) }));
vi.mock("../context", () => ({ useApp: () => ({ workspace: { slug: "ws" }, can: () => true }) }));
vi.mock("./ScriptEditorDialog", () => ({ ScriptEditorDialog: ({ node, editable }: { node: { name: string }; editable: boolean }) => <div role="dialog" aria-label="script">{node.name} editable={String(editable)}</div> }));
vi.mock("./CanvasView", () => ({
  CanvasView: ({ graph, onSelect, onOpen, readOnly }: { graph: Graph; onSelect: (id: string) => void; onOpen: (id: string) => void; readOnly?: boolean }) => (
    <div data-readonly={String(readOnly)}>
      {graph.nodes.map((n) => (
        <button key={n.id} type="button" onClick={() => onSelect(n.id)} onDoubleClick={() => onOpen(n.id)}>{n.name}</button>
      ))}
    </div>
  ),
}));
vi.mock("./NodePanel", () => ({
  NodePanel: ({ node, editable, preview }: { node: { name: string }; editable: boolean; preview?: boolean }) => <p>panneau {node.name} editable={String(editable)} preview={String(preview)}</p>,
}));

import { DraftProviderForTest } from "./testDraft";

describe("Aperçu du workflow appelé", () => {
  it("shows the called workflow read only, with a read-only properties panel", async () => {
    render(<DraftProviderForTest><WorkflowPreview slug="enfant" onClose={() => undefined} /></DraftProviderForTest>);
    expect(screen.getByRole("dialog", { name: /Enfant \(lecture seule\)/ })).toBeInTheDocument();
    expect(screen.getByText("Calcul").closest("div")?.getAttribute("data-readonly")).toBe("true");
    await userEvent.click(screen.getByText("Calcul"));
    expect(screen.getByText(/panneau Calcul editable=false preview=true/)).toBeInTheDocument();
  });

  it("opens a script read only on double click, and opens nothing for a called-workflow step", async () => {
    render(<DraftProviderForTest><WorkflowPreview slug="enfant" onClose={() => undefined} /></DraftProviderForTest>);
    await userEvent.dblClick(screen.getByText("Petit-enfant"));
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    await userEvent.dblClick(screen.getByText("Calcul"));
    expect(screen.getByRole("dialog", { name: "script" })).toHaveTextContent("Calcul editable=false");
  });
});
