import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { emptyGraph } from "../graphOps";
import type { Graph } from "../types";
import { VariablesTab } from "./VariablesTab";

let graph: Graph;
const setGraph = vi.fn((g: Graph) => {
  graph = g;
});
vi.mock("../DraftContext", () => ({ useDraft: () => ({ graph, setGraph, editable: true }) }));

const withVar = (type: "json" | "string", value: unknown): Graph => ({ ...emptyGraph(), variables: [{ name: "conf", type, value }] });

describe("variable de type JSON", () => {
  beforeEach(() => setGraph.mockClear());

  it("propose le type JSON dans la liste des types", () => {
    graph = withVar("string", "");
    render(<VariablesTab />);
    expect(screen.getByRole("option", { name: "JSON" })).toBeInTheDocument();
  });

  it("affiche la valeur mise en forme dans une zone de texte, pas dans un champ simple", () => {
    graph = withVar("json", { a: 1, b: [1, 2] });
    render(<VariablesTab />);
    const box = screen.getByLabelText("Valeur de conf") as HTMLTextAreaElement;
    expect(box.tagName).toBe("TEXTAREA");
    expect(box.value).toBe(JSON.stringify({ a: 1, b: [1, 2] }, null, 2));
    expect(screen.getByText("JSON valide.")).toBeInTheDocument();
  });

  it("enregistre une valeur valide, signale une syntaxe invalide sans écraser la dernière valeur, et formate à la demande", async () => {
    graph = withVar("json", null);
    render(<VariablesTab />);
    const box = screen.getByLabelText("Valeur de conf");
    await userEvent.click(box);
    await userEvent.paste('{"x":1,"y":[true]}');
    expect(setGraph).toHaveBeenLastCalledWith(expect.objectContaining({ variables: [expect.objectContaining({ value: { x: 1, y: [true] } })] }));
    await userEvent.type(box, "{{");
    expect(screen.getByRole("alert")).toHaveTextContent("JSON invalide");
    expect(screen.getAllByRole("button", { name: "Formater" })[0] as HTMLElement).toBeDisabled();
    const calls = setGraph.mock.calls.length;
    await userEvent.type(box, "{Backspace}");
    expect(screen.getAllByRole("button", { name: "Formater" })[0] as HTMLElement).toBeEnabled();
    await userEvent.click(screen.getAllByRole("button", { name: "Formater" })[0] as HTMLElement);
    expect((box as HTMLTextAreaElement).value).toContain('\n  "x": 1');
    expect(setGraph.mock.calls.length).toBeGreaterThanOrEqual(calls);
  });

  it("saisit aussi les valeurs par environnement en JSON mis en forme, et un champ vidé retire l'environnement", async () => {
    graph = { ...emptyGraph(), variables: [{ name: "conf", type: "json", value: {}, env_values: { prod: { a: 1 } } }] };
    render(<VariablesTab />);
    const prod = screen.getByLabelText(/Production/) as HTMLTextAreaElement;
    expect(prod.tagName).toBe("TEXTAREA");
    expect(prod.value).toBe(JSON.stringify({ a: 1 }, null, 2));
    await userEvent.clear(prod);
    expect(setGraph).toHaveBeenLastCalledWith(expect.objectContaining({ variables: [expect.objectContaining({ env_values: undefined })] }));
    await userEvent.click(screen.getByLabelText(/Recette/));
    await userEvent.paste('{"b":[2]}');
    expect(setGraph).toHaveBeenLastCalledWith(
      expect.objectContaining({ variables: [expect.objectContaining({ env_values: { prod: { a: 1 }, test: { b: [2] } } })] }),
    );
  });
});
