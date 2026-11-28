import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
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

const wf = (slug: string, name: string, folder: string) => ({
  slug,
  name,
  folder,
  description: "",
  owner: null,
  draft_revision: 1,
  updated_at: "2026-11-16T10:00:00Z",
  deprecated: false,
  latest_version: null,
  tags: [],
});

describe("Recherche dans une liste à arborescence", () => {
  beforeEach(() => {
    api.mockReset();
    apiAll.mockReset();
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })) as unknown as typeof window.matchMedia;
    api.mockImplementation(async () => ({ results: [], count: 0, next: null }));
    apiAll.mockImplementation(async () => ({
      results: [wf("alpha-flow", "Alpha", "Infra"), wf("bravo-flow", "Bravo", "Reseau"), wf("charlie-flow", "Charlie", "Reseau")],
      count: 3,
      next: null,
    }));
  });

  function show() {
    const ctx = { workspace: { slug: "team-a" }, can: () => true } as unknown as Ctx;
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

  it("cherche dans tous les dossiers, par nom ou par identifiant, même quand un dossier est choisi", async () => {
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /^Infra/ }));
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.queryByText("Bravo")).toBeNull(); // sans recherche, le dossier limite la liste
    const search = screen.getByRole("searchbox", { name: "Rechercher un workflow" });
    await user.type(search, "bravo");
    expect(await screen.findByText("Bravo")).toBeInTheDocument(); // par le nom, dans un autre dossier
    await user.clear(search);
    await user.type(search, "charlie-flow");
    expect(await screen.findByText("Charlie")).toBeInTheDocument(); // par l'identifiant
    expect(screen.queryByText("Alpha")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("tous dossiers");
    await user.clear(search);
    expect(screen.queryByText("Bravo")).toBeNull(); // sans recherche : retour au dossier choisi
    expect(screen.getByText("Alpha")).toBeInTheDocument();
  });
});
