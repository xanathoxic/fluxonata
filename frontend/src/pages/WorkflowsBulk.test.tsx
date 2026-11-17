import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { WorkflowsPage } from "./WorkflowsPage";

const api = vi.fn();
const apiAll = vi.fn();
vi.mock("../api", async (orig) => ({
  ...(await orig<typeof import("../api")>()),
  api: (...a: unknown[]) => api(...a),
  apiAll: (...a: unknown[]) => apiAll(...a),
}));
vi.mock("../components/FavoriteStar", () => ({ FavoriteStar: () => null }));
vi.mock("../components/AiBox", () => ({ useAiMode: () => ({ enabled: false, mode: "off" }) }));

const wf = (slug: string, name: string, extra = {}) => ({
  slug,
  name,
  folder: "",
  description: "",
  owner: null,
  draft_revision: 1,
  updated_at: "2026-11-16T10:00:00Z",
  deprecated: false,
  latest_version: null,
  tags: [],
  ...extra,
});
const LIST = [wf("a", "Alpha"), wf("b", "Bravo", { latest_version: { version: "1.0.0" } }), wf("c", "Charlie")];

function show(can: (p: string) => boolean = () => true) {
  const ctx = { workspace: { slug: "team-a" }, can } as unknown as Ctx;
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}>
        <MemoryRouter>
          <WorkflowsPage />
        </MemoryRouter>
      </AppContext.Provider>
    </QueryClientProvider>,
  );
}
const posted = () => api.mock.calls.filter((c) => String(c[0]).endsWith("/workflows/publish-many/"));

describe("Workflows : sélection multiple et publication groupée", () => {
  beforeEach(() => {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })) as unknown as typeof window.matchMedia; // absent de jsdom
    api.mockReset();
    apiAll.mockReset();
    apiAll.mockResolvedValue({ results: LIST, count: 3, next: null });
  });

  it("cocher des workflows affiche la barre de sélection avec leur nombre ; tout désélectionner la retire", async () => {
    show();
    await userEvent.click(await screen.findByRole("checkbox", { name: "Sélectionner Alpha" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Sélectionner Charlie" }));
    const bar = screen.getByRole("region", { name: "Actions sur la sélection" });
    expect(within(bar).getByText("2 workflows sélectionnés")).toBeInTheDocument();
    await userEvent.click(within(bar).getByRole("button", { name: "Tout désélectionner" }));
    expect(screen.queryByRole("region", { name: "Actions sur la sélection" })).toBeNull();
  });

  it("la case d'en-tête sélectionne toute la page", async () => {
    show();
    await userEvent.click(await screen.findByRole("checkbox", { name: "Sélectionner tous les workflows de la page" }));
    expect(screen.getByText("3 workflows sélectionnés")).toBeInTheDocument();
  });

  it("sans le droit de publier, ni cases ni barre", async () => {
    show((p) => p !== "publish");
    await screen.findByText("Alpha");
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("publie la sélection avec le type de changement choisi, puis affiche le résultat de chacun", async () => {
    const reply = {
      published: 1,
      skipped: 1,
      error: 1,
      results: [
        { slug: "a", name: "Alpha", status: "published", version: "1.0.0" },
        { slug: "b", name: "Bravo", status: "skipped", version: "1.0.0", message: "Aucune modification depuis la v1.0.0." },
        { slug: "c", name: "Charlie", status: "error", message: "Le workflow est vide." },
      ],
    };
    api.mockImplementation(async (path: string) => (String(path).endsWith("/publish-many/") ? reply : { results: [] }));
    show();
    for (const n of ["Alpha", "Bravo", "Charlie"]) await userEvent.click(await screen.findByRole("checkbox", { name: `Sélectionner ${n}` }));
    await userEvent.click(screen.getByRole("button", { name: "Publier la sélection" }));
    const dialog = await screen.findByRole("dialog", { name: "Publier 3 workflows" });
    await userEvent.click(within(dialog).getByLabelText(/Correction/));
    await userEvent.type(within(dialog).getByLabelText(/Note de version/), "lot de novembre");
    await userEvent.click(within(dialog).getByRole("button", { name: "Publier les 3" }));
    await waitFor(() => expect(posted()).toHaveLength(1));
    expect(posted()[0]?.[1]).toEqual({ json: { slugs: ["a", "b", "c"], bump: "patch", changelog: "lot de novembre" } });
    const result = await screen.findByRole("list", { name: "Résultat par workflow" });
    expect(within(result).getByText(/Alpha/).closest("li")).toHaveTextContent("publié (v1.0.0)");
    expect(within(result).getByText(/Bravo/).closest("li")).toHaveTextContent("inchangé (v1.0.0) : Aucune modification depuis la v1.0.0.");
    expect(
      within(result)
        .getByText(/Charlie/)
        .closest("li"),
    ).toHaveTextContent("refusé : Le workflow est vide.");
    expect(screen.getByText("1 publié(s), 1 inchangé(s), 1 refusé(s).")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "Fermer" }).at(-1) as HTMLElement);
    expect(screen.queryByRole("region", { name: "Actions sur la sélection" })).toBeNull(); // la sélection est vidée
  });

  it("une panne du serveur est expliquée et rien n'est présenté comme publié", async () => {
    api.mockRejectedValue(Object.assign(new Error("Erreur serveur"), { status: 500 }));
    show();
    await userEvent.click(await screen.findByRole("checkbox", { name: "Sélectionner Alpha" }));
    await userEvent.click(screen.getByRole("button", { name: "Publier la sélection" }));
    await userEvent.click(await screen.findByRole("button", { name: "Publier le workflow" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Erreur serveur");
    expect(screen.queryByRole("list", { name: "Résultat par workflow" })).toBeNull();
  });

  it("le bouton Supprimer d'une ligne ouvre la recherche des workflows qui l'utilisent", async () => {
    api.mockResolvedValue({ used_by: [{ slug: "b", name: "Bravo", where: ["v1.0.0"] }], hidden: 0, total: 1 });
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Supprimer Alpha" }));
    expect(await screen.findByText(/Suppression impossible : 1 workflow appelle le workflow\./)).toBeInTheDocument();
    expect(api).toHaveBeenCalledWith("/workspaces/team-a/workflows/a/usage/");
  });
});
