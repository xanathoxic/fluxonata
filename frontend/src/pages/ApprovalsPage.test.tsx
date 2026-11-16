import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { ApprovalsPage } from "./ApprovalsPage";

const api = vi.fn();
vi.mock("../api", async (orig) => ({ ...(await orig<typeof import("../api")>()), api: (...a: unknown[]) => api(...a) }));

const BASE = {
  id: 5,
  title: "Valider l'achat",
  instructions: "",
  subject: "Achat n°42 — serveurs",
  body: "## Détail\n\nLe montant est de **12 000 €**.\n\n- un\n- deux",
  status: "pending",
  requested_by: "alice",
  decided_by: null,
  comment: "",
  created_at: "2026-11-16T09:00:00Z",
  decided_at: null,
  run_id: "r-1",
  workflow: "Achats",
  context: { montant: 12000, fournisseur: "Acme" },
  can_decide: true,
};

function serve(ap: object = BASE) {
  api.mockImplementation(async (path: string) => {
    if (path.includes("/approvals/")) return { results: [{ ...BASE, ...ap }] };
    if (path.includes("/promotions/")) return { results: [] };
    return {};
  });
}

function show(can = true) {
  const ctx = { workspace: { slug: "team-a" }, can: () => can } as unknown as Ctx;
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}>
        <MemoryRouter>
          <ApprovalsPage />
        </MemoryRouter>
      </AppContext.Provider>
    </QueryClientProvider>,
  );
}
async function open() {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Examiner : Valider l'achat" }));
  return { user, dialog: await screen.findByRole("dialog") };
}
const decisions = () => api.mock.calls.filter((c) => String(c[0]).endsWith("/decide/"));

describe("Approbations : fenêtre d'une demande", () => {
  beforeEach(() => {
    api.mockReset();
    serve();
  });

  it("est centrée, élargie, et composée de deux onglets", async () => {
    show();
    const { dialog } = await open();
    expect(dialog.closest(".overlay-backdrop")?.className).toMatch(/overlay-modal/);
    expect(dialog.closest(".overlay-backdrop")?.className).toMatch(/overlay-approval/);
    const tabs = within(dialog)
      .getAllByRole("tab")
      .map((t) => t.textContent);
    expect(tabs).toEqual(["Demande", "Contexte de la demande"]);
  });

  it("montre dans l'onglet Demande le titre, le demandeur, l'objet et le corps mis en forme en Markdown", async () => {
    show();
    const { dialog } = await open();
    const d = within(dialog);
    expect(d.getAllByText("Valider l'achat").length).toBeGreaterThan(0);
    expect(d.getByText("alice")).toBeInTheDocument();
    expect(d.getAllByText("Achat n°42 — serveurs").length).toBeGreaterThan(0);
    expect(d.getByRole("heading", { level: 4, name: "Détail" })).toBeInTheDocument();
    expect(d.getByText("12 000 €").tagName).toBe("STRONG");
    expect(d.getAllByRole("listitem").map((l) => l.textContent)).toEqual(["un", "deux"]);
  });

  it("montre le contexte dans le second onglet", async () => {
    show();
    const { user, dialog } = await open();
    await user.click(within(dialog).getByRole("tab", { name: "Contexte de la demande" }));
    expect(within(dialog).getByText("Acme")).toBeInTheDocument();
    expect(within(dialog).queryByRole("heading", { name: "Détail" })).toBeNull();
    expect(within(dialog).getByRole("link", { name: "Voir l'exécution" })).toBeInTheDocument();
  });

  it("garde le commentaire et les boutons sous les deux onglets, et envoie la décision avec le commentaire", async () => {
    show();
    const { user, dialog } = await open();
    const d = within(dialog);
    for (const name of ["Demande", "Contexte de la demande"]) {
      await user.click(d.getByRole("tab", { name }));
      expect(d.getByLabelText("Commentaire (facultatif)")).toBeInTheDocument();
      expect(d.getByRole("button", { name: "Approuver" })).toBeEnabled();
      expect(d.getByRole("button", { name: "Refuser" })).toBeEnabled();
    }
    await user.type(d.getByLabelText("Commentaire (facultatif)"), "Ok pour moi");
    await user.click(d.getByRole("tab", { name: "Demande" }));
    expect(d.getByLabelText("Commentaire (facultatif)")).toHaveValue("Ok pour moi");
    await user.click(d.getByRole("button", { name: "Approuver" }));
    expect(decisions()).toHaveLength(1);
    expect(decisions()[0]?.[1]).toMatchObject({ json: { approve: true, comment: "Ok pour moi" } });
  });

  it("ne laisse pas décider de sa propre demande ni sans le droit d'approuver", async () => {
    serve({ can_decide: false });
    show();
    const { dialog } = await open();
    expect(within(dialog).getByRole("button", { name: "Approuver" })).toBeDisabled();
    expect(within(dialog).getByText(/propre demande/)).toBeInTheDocument();
  });

  it("sans droit d'approuver : ni commentaire ni boutons", async () => {
    show(false);
    const { dialog } = await open();
    expect(within(dialog).queryByRole("button", { name: "Approuver" })).toBeNull();
    expect(within(dialog).getByText("Votre rôle ne permet pas de décider.")).toBeInTheDocument();
  });

  it("affiche l'ancienne consigne en Markdown quand la demande n'a pas de corps", async () => {
    serve({ body: "", subject: "", instructions: "Vérifiez le **budget**." });
    show();
    const { dialog } = await open();
    expect(within(dialog).getByText("budget").tagName).toBe("STRONG");
  });

  it("dit qu'aucun détail n'accompagne une demande sans corps ni consigne", async () => {
    serve({ body: "", subject: "", instructions: "" });
    show();
    const { dialog } = await open();
    expect(within(dialog).getByText("Aucun détail n'accompagne cette demande.")).toBeInTheDocument();
  });

  it("rend le corps comme du texte : aucune balise écrite par l'auteur n'est injectée", async () => {
    serve({ body: "<img src=x onerror=alert(1)> [x](javascript:alert(1))" });
    show();
    const { dialog } = await open();
    expect(dialog.querySelector("img")).toBeNull();
    expect(dialog.querySelector("a[href^='javascript']")).toBeNull();
  });
});
