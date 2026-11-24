import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { ModelConfig, ModelEditorDialog } from "./ModelEditor";
import type { GraphNode } from "./types";

const api = vi.fn();
vi.mock("../api", async (orig) => ({ ...(await orig<typeof import("../api")>()), api: (...a: unknown[]) => api(...a) }));

const IMAGES = [
  {
    id: "img-1",
    name: "logo.png",
    content_type: "image/png",
    width: 40,
    height: 20,
    bytes: 2048,
    url: "/api/v1/workspaces/demo/model-images/img-1/",
    reference: "image:img-1",
  },
];
const VARS = [{ path: "inputs.nom", label: "Entrée → nom" }];

function wire(over: Record<string, unknown> = {}) {
  api.mockImplementation(async (path: string, init?: { method?: string; json?: { markdown: string } }) => {
    if (over[path]) return over[path];
    if (path.endsWith("/model-preview/")) return { html: `<p>aperçu : ${init?.json?.markdown.length}</p>`, missing_images: [] };
    if (path.endsWith("/model-images/") && init?.method === "POST") return { ...IMAGES[0], id: "img-2", name: "photo.jpg" };
    if (path.endsWith("/model-images/")) return IMAGES;
    return {};
  });
}

function show(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ctx = { workspace: { slug: "demo", name: "Démo", role: "designer" }, can: () => true } as unknown as Ctx;
  return render(
    <QueryClientProvider client={client}>
      <AppContext.Provider value={ctx}>{ui}</AppContext.Provider>
    </QueryClientProvider>,
  );
}

function dialog(over: Partial<React.ComponentProps<typeof ModelEditorDialog>> = {}) {
  const onApply = vi.fn();
  const onClose = vi.fn();
  show(
    <ModelEditorDialog
      nodeName="Mon modèle"
      initial={{ markdown: "# Salut", title: "" }}
      variables={VARS}
      readOnly={false}
      onApply={onApply}
      onClose={onClose}
      {...over}
    />,
  );
  return { onApply, onClose };
}

describe("Éditeur de modèle de document", () => {
  beforeEach(() => {
    api.mockReset();
    wire();
  });

  it("s'ouvre en overlay avec le texte à gauche et l'aperçu rendu par le serveur à droite", async () => {
    dialog();
    expect(screen.getByRole("dialog", { name: "Modèle de document : Mon modèle" })).toBeInTheDocument();
    expect(screen.getByLabelText("Texte (Markdown)")).toHaveValue("# Salut");
    await waitFor(() => expect(screen.getByRole("region", { name: "Aperçu" })).toHaveTextContent("aperçu : 7"));
  });

  it("met à jour l'aperçu quand on écrit", async () => {
    dialog();
    await userEvent.type(screen.getByLabelText("Texte (Markdown)"), "!!");
    await waitFor(() => expect(screen.getByRole("region", { name: "Aperçu" })).toHaveTextContent("aperçu : 9"));
  });

  it("met en forme la sélection avec la barre et le clavier", async () => {
    dialog({ initial: { markdown: "un mot ici", title: "" } });
    const area = screen.getByLabelText("Texte (Markdown)") as HTMLTextAreaElement;
    area.focus();
    area.setSelectionRange(3, 6);
    await userEvent.click(screen.getByRole("button", { name: "Gras (Ctrl+B)" }));
    expect(area.value).toBe("un **mot** ici");
    area.setSelectionRange(0, 2);
    await userEvent.keyboard("{Control>}i{/Control}");
    expect(area.value).toBe("*un* **mot** ici");
  });

  it("insère une variable et propose les outils de mise en forme", async () => {
    dialog({ initial: { markdown: "Bonjour ", title: "" } });
    const area = screen.getByLabelText("Texte (Markdown)") as HTMLTextAreaElement;
    area.focus();
    area.setSelectionRange(8, 8);
    await userEvent.selectOptions(screen.getByLabelText("Insérer une variable"), "inputs.nom");
    expect(area.value).toBe("Bonjour {{ inputs.nom }}");
    const bar = screen.getByRole("toolbar", { name: "Mise en forme" });
    for (const name of ["Titre 1", "Barré", "Liste à puces", "Liste numérotée", "Citation", "Tableau", "Séparateur", "Ajouter une image"])
      expect(bar).toContainElement(screen.getByRole("button", { name }));
  });

  it("envoie une image et insère sa référence ; liste et insère celles de l'espace", async () => {
    dialog({ initial: { markdown: "", title: "" } });
    const file = new File([new Uint8Array([1, 2, 3])], "photo.jpg", { type: "image/jpeg" });
    await userEvent.upload(document.querySelector('input[type="file"]') as HTMLInputElement, file);
    await waitFor(() => expect((screen.getByLabelText("Texte (Markdown)") as HTMLTextAreaElement).value).toBe("![photo](image:img-2)"));
    const call = api.mock.calls.find((c) => c[0].endsWith("/model-images/") && c[1]?.method === "POST");
    expect(call?.[1].form.get("file").name).toBe("photo.jpg");
    await userEvent.click(screen.getByText(/Images de l'espace/));
    await userEvent.click(await screen.findByRole("button", { name: "Insérer" }));
    expect((screen.getByLabelText("Texte (Markdown)") as HTMLTextAreaElement).value).toContain("![logo](image:img-1)");
  });

  it("montre l'erreur du serveur à l'envoi d'une image refusée", async () => {
    api.mockImplementation(async (path: string, init?: { method?: string }) => {
      if (path.endsWith("/model-images/") && init?.method === "POST")
        throw { status: 400, error: { message: "Format non pris en charge : utilisez PNG, JPEG ou GIF." } };
      return path.endsWith("/model-preview/") ? { html: "", missing_images: [] } : [];
    });
    dialog();
    await userEvent.upload(document.querySelector('input[type="file"]') as HTMLInputElement, new File(["x"], "a.png", { type: "image/png" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("signale les images introuvables et les erreurs d'aperçu", async () => {
    wire({ "/workspaces/demo/model-preview/": { html: "<p>x</p>", missing_images: ["a", "b"] } });
    dialog();
    expect(await screen.findByText(/2 image\(s\) introuvable\(s\)/)).toBeInTheDocument();
  });

  it("n'applique rien avant « Appliquer » et n'applique que ce qui a changé", async () => {
    const { onApply, onClose } = dialog();
    expect(screen.getByRole("button", { name: "Appliquer" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Titre du document (facultatif)"), "Bilan");
    await userEvent.type(screen.getByLabelText("Texte (Markdown)"), " !");
    await userEvent.click(screen.getByRole("button", { name: "Appliquer" }));
    expect(onApply).toHaveBeenCalledWith({ markdown: "# Salut !", title: "Bilan" });
    await userEvent.click(screen.getByRole("button", { name: "Annuler" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("en lecture seule : texte non modifiable, ni barre d'outils ni bouton Appliquer", () => {
    dialog({ readOnly: true });
    expect(screen.getByLabelText("Texte (Markdown)")).toHaveAttribute("readonly");
    expect(screen.queryByRole("toolbar")).toBeNull();
    expect(screen.queryByRole("button", { name: "Appliquer" })).toBeNull();
    expect(screen.getAllByRole("button", { name: "Fermer" })).toHaveLength(2); // la croix de l'overlay et le pied de page
  });
});

describe("Onglet Modèle de l'étape", () => {
  beforeEach(() => {
    api.mockReset();
    wire();
  });
  const node: GraphNode = { id: "modele", type: "model", name: "Rapport", markdown: "# Bilan du mois\n\n![a](image:x) texte", title: "" };

  it("résume le modèle et ouvre l'éditeur au centre ; « Appliquer » met à jour l'étape", async () => {
    const onUpdate = vi.fn();
    show(<ModelConfig node={node} variables={VARS} editable onUpdate={onUpdate} />);
    expect(screen.getByText("Bilan du mois")).toBeInTheDocument();
    expect(screen.getByText(/1 image\(s\)/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Ouvrir l'éditeur" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Texte (Markdown)"), "!");
    await userEvent.click(screen.getByRole("button", { name: "Appliquer" }));
    expect(onUpdate).toHaveBeenCalledWith({ markdown: `${node.markdown}!`, title: undefined });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("sans droit de modification, propose seulement de voir", () => {
    show(<ModelConfig node={node} variables={[]} editable={false} onUpdate={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Voir le modèle" })).toBeInTheDocument();
  });
});
