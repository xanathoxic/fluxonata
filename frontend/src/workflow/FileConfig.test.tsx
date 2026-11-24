import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { FILE_FORMATS, FileConfig } from "./FileConfig";
import { defaultNode, summarize } from "./graphOps";
import type { GraphNode } from "./types";

function Harness({ initial, onChange, models }: { initial: GraphNode; onChange?: (n: GraphNode) => void; models?: Pick<GraphNode, "id" | "name">[] }) {
  const [node, setNode] = useState(initial);
  return (
    <FileConfig
      models={models}
      node={node}
      onUpdate={(patch) => {
        const next = { ...node, ...patch };
        setNode(next);
        onChange?.(next);
      }}
    />
  );
}
const base: GraphNode = { id: "f", type: "file", name: "Rapport", format: "pdf", filename: "rapport", content: "" };

describe("Créer un fichier", () => {
  it("propose texte, CSV, Excel, PDF, Word, PowerPoint et ZIP", () => {
    expect(FILE_FORMATS.map((f) => f.value)).toEqual(["text", "csv", "xlsx", "pdf", "docx", "pptx", "html", "zip"]);
    render(<Harness initial={base} />);
    expect(screen.getByRole("option", { name: "Présentation PowerPoint (.pptx)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Tableur Excel (.xlsx)" })).toBeInTheDocument();
  });

  it("montre les bons champs selon le format", async () => {
    render(<Harness initial={base} />);
    expect(screen.getByLabelText("Contenu")).toBeInTheDocument();
    expect(screen.getByLabelText("Titre (facultatif)")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Format du fichier"), "pptx");
    expect(screen.getByLabelText("Diapositives")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Format du fichier"), "xlsx");
    expect(screen.getByLabelText("Lignes du tableau")).toBeInTheDocument();
    expect(screen.getByLabelText("Nom de la feuille (facultatif)")).toBeInTheDocument();
    expect(screen.queryByLabelText("Contenu")).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText("Format du fichier"), "zip");
    expect(screen.getByLabelText("Fichiers à archiver")).toBeInTheDocument();
  });

  it("range les colonnes en liste et les fichiers de l'archive un par ligne", async () => {
    let last = { ...base, format: "csv" } as GraphNode;
    const { unmount } = render(<Harness initial={last} onChange={(n) => (last = n)} />);
    await userEvent.type(screen.getByLabelText("Colonnes (facultatif)"), "nom, service");
    expect(last.columns).toEqual(["nom", "service"]);
    unmount();
    render(<Harness initial={{ ...base, format: "zip" }} onChange={(n) => (last = n)} />);
    await userEvent.type(screen.getByLabelText("Fichiers à archiver"), "{{{{ steps.a.output.file }}{enter}{{{{ steps.b.output.file }}");
    expect(last.sources).toEqual(["{{ steps.a.output.file }}", "{{ steps.b.output.file }}"]);
  });

  it("dit où télécharger le fichier", () => {
    render(<Harness initial={base} />);
    expect(screen.getByText(/télécharge depuis la page de l'exécution/)).toBeInTheDocument();
  });

  it("crée une étape prête à l'emploi et la résume", () => {
    const n = defaultNode({ inputs: { type: "object", properties: {}, required: [] }, nodes: [], edges: [], outputs: {} }, "file");
    expect(n).toMatchObject({ type: "file", format: "pdf", filename: "rapport" });
    expect(summarize(n, [])).toBe("Fichier PDF · rapport");
  });

  it("propose les modèles de document du workflow et n'affiche plus le contenu quand l'un est choisi", async () => {
    let last = base;
    render(<Harness initial={base} models={[{ id: "modele", name: "Rapport mensuel" }]} onChange={(n) => (last = n)} />);
    expect(screen.getByLabelText("Contenu")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Modèle de document"), "modele");
    expect(last.model).toBe("{{ steps.modele.output }}");
    expect(screen.queryByLabelText("Contenu")).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText("Modèle de document"), "");
    expect(last.model).toBeUndefined();
    expect(screen.getByLabelText("Contenu")).toBeInTheDocument();
  });

  it("explique ce que chaque format reprend du modèle, et invite à en ajouter un s'il n'y en a pas", async () => {
    render(<Harness initial={{ ...base, format: "pdf" }} />);
    expect(screen.getByText(/Ajoutez d'abord une étape/)).toBeInTheDocument();
    expect(screen.getByText(/Mise en page complète/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Format du fichier"), "docx");
    expect(screen.getByText(/images et tableaux compris/)).toBeInTheDocument();
  });
});
