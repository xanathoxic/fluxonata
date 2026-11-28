import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { StepRun, WorkflowRun } from "../api";
import { RunInspectorPanel } from "./RunInspector";

const step = (id: string, name: string, logs?: string): StepRun => ({
  node_id: id, name, node_type: "script", status: "succeeded", status_label: "Réussie", attempt: 1, output: { resume: `sortie ${name}` }, error: null,
  available_at: null, started_at: "2026-11-08T10:00:00Z", finished_at: "2026-11-08T10:00:01Z", approval_id: null, execution_id: null, child_run_ids: [], ...(logs === undefined ? {} : { logs }),
});

const run = {
  id: "r1", status: "succeeded", graph: { nodes: [{ id: "a", name: "Etape_A", type: "script", ui: null }, { id: "b", name: "Etape_B", type: "script", ui: null }], edges: [] },
  steps: [step("a", "Etape_A", "[INFO] journal de A"), step("b", "Etape_B", "[INFO] journal de B"), step("c", "Etape_C")],
  inspector: { start: { inputs: {}, constants: {} }, nodes: { a: { inputs: { mot_de_passe: "••••" } }, b: { inputs: null }, c: { inputs: null } }, end: { outputs: null } },
} as unknown as WorkflowRun;

describe("inspecteur d'exécution : journal de l'étape sélectionnée", () => {
  it("affiche le journal de l'étape sélectionnée dans un onglet « Journal », associé à son nom", async () => {
    render(<RunInspectorPanel run={run} selectedId="a" />);
    expect(screen.getByRole("tab", { name: "Variables", selected: true })).toBeInTheDocument();
    expect(screen.queryByLabelText("Journal de Etape_A")).toBeNull(); // les variables d'abord, le journal sur demande
    await userEvent.click(screen.getByRole("tab", { name: "Journal" }));
    expect(screen.getByLabelText("Journal de Etape_A")).toHaveTextContent("journal de A");
    expect(screen.queryByText("sortie Etape_A")).toBeNull();
  });
  it("ne montre jamais le journal d'une autre étape quand la sélection change (et revient sur Variables)", async () => {
    const { rerender } = render(<RunInspectorPanel run={run} selectedId="a" />);
    await userEvent.click(screen.getByRole("tab", { name: "Journal" }));
    rerender(<RunInspectorPanel run={run} selectedId="b" />);
    expect(screen.getByRole("tab", { name: "Variables", selected: true })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Journal" }));
    expect(screen.getByLabelText("Journal de Etape_B")).toHaveTextContent("journal de B");
    expect(screen.queryByLabelText("Journal de Etape_A")).toBeNull();
    expect(screen.queryByText(/journal de A/)).toBeNull();
  });
  it("n'affiche aucun onglet Journal pour une étape sans journal, ni pour Début et Fin", () => {
    const { rerender } = render(<RunInspectorPanel run={run} selectedId="c" />);
    expect(screen.queryByLabelText(/Journal de/)).toBeNull();
    expect(screen.queryByRole("tab")).toBeNull();
    rerender(<RunInspectorPanel run={run} selectedId="__start__" />);
    expect(screen.queryByLabelText(/Journal de/)).toBeNull();
    rerender(<RunInspectorPanel run={run} selectedId="__end__" />);
    expect(screen.queryByLabelText(/Journal de/)).toBeNull();
  });
  it("garde les entrées masquées par le serveur (aucune valeur en clair ajoutée)", () => {
    render(<RunInspectorPanel run={run} selectedId="a" />);
    expect(screen.getByText("••••")).toBeInTheDocument();
  });
});
