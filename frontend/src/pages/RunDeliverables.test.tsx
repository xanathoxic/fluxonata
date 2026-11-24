import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunDeliverables, sizeLabel, type RunFileRow } from "./RunDeliverables";

const row = (id: string, name: string, size = 1500): RunFileRow => ({ id, step_id: "f", name, size, content_type: "application/pdf", sha256: "x" });

describe("Fichiers à télécharger", () => {
  it("n'affiche rien sans fichier", () => {
    const { container } = render(<RunDeliverables rows={[]} slug="demo" runId="r1" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("propose un fichier seul sans archive", () => {
    render(<RunDeliverables rows={[row("a", "rapport.pdf")]} slug="demo" runId="r1" />);
    expect(screen.getByRole("heading", { name: "Fichier à télécharger" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "rapport.pdf" })).toHaveAttribute("href", "/api/v1/workspaces/demo/run-files/a/download/");
    expect(screen.queryByRole("link", { name: /Tout télécharger/ })).toBeNull();
  });

  it("propose chaque fichier et une archive de tout pour plusieurs fichiers", () => {
    render(<RunDeliverables rows={[row("a", "rapport.pdf"), row("b", "bilan.xlsx", 3 * 1048576)]} slug="demo" runId="r1" />);
    expect(screen.getByRole("heading", { name: "2 fichiers à télécharger" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "bilan.xlsx" })).toBeInTheDocument();
    expect(screen.getByText("(3.0 Mo)")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Tout télécharger (.zip)" })).toHaveAttribute("href", "/api/v1/workspaces/demo/workflow-runs/r1/files/archive/");
  });

  it("écrit les tailles lisiblement", () => {
    expect([sizeLabel(12), sizeLabel(1536), sizeLabel(2 * 1048576)]).toEqual(["12 o", "1.5 Ko", "2.0 Mo"]);
  });
});
