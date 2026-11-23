import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { ExecutionsPage } from "./ExecutionsPage";

const apiAll = vi.fn();
const api = vi.fn();
vi.mock("../api", async (orig) => ({
  ...(await orig<typeof import("../api")>()),
  apiAll: (...a: unknown[]) => apiAll(...a),
  api: (...a: unknown[]) => api(...a),
}));

const base = { status: "succeeded", status_label: "Réussie", needs_decision: false, environment: "test", created_at: "2026-11-12T08:00:00Z" };
const PARENT = { ...base, id: "p", workflow: { name: "Parent" }, has_children: true };
const ALONE = { ...base, id: "a", workflow: { name: "Seul" }, has_children: false };
const CHILD = { ...base, id: "c", workflow: { name: "Enfant" }, has_children: true, step_name: "Appeler", child_index: null };
const GRAND = { ...base, id: "g", workflow: { name: "Petit-enfant" }, has_children: false, step_name: "Boucle", child_index: 1 };

function show() {
  apiAll.mockImplementation(async (url?: string) =>
    url?.includes("/workflow-runs/") ? { results: [PARENT, ALONE], count: 2, next: null } : { results: [], count: 0, next: null },
  );
  api.mockImplementation(async (path: string) => (path.includes("/p/children/") ? [CHILD] : path.includes("/c/children/") ? [GRAND] : []));
  const ctx = { workspace: { slug: "team-a" } } as unknown as Ctx;
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}>
        <MemoryRouter>
          <ExecutionsPage />
        </MemoryRouter>
      </AppContext.Provider>
    </QueryClientProvider>,
  );
}

describe("Exécutions : sous-workflows repliables sous leur parent", () => {
  beforeEach(() => {
    apiAll.mockReset();
    api.mockReset();
  });

  it("seule une exécution qui a des sous-workflows porte un bouton de dépliage ; ils sont repliés au départ", async () => {
    show();
    expect(await screen.findByText("Parent")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /sous-workflows de/ })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Déplier les sous-workflows de Parent" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Enfant")).not.toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
  });

  it("déplier montre le sous-workflow (étape, type), replier le masque, et un sous-workflow se déplie à son tour", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Déplier les sous-workflows de Parent" }));
    const child = (await screen.findByText("Enfant")).closest("tr") as HTMLElement;
    expect(within(child).getByText(/étape « Appeler »/)).toBeInTheDocument();
    expect(within(child).getByText("Sous-workflow")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Déplier les sous-workflows de Enfant" }));
    const grand = (await screen.findByText("Petit-enfant")).closest("tr") as HTMLElement;
    expect(within(grand).getByText(/n° 2/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Replier les sous-workflows de Parent" }));
    expect(screen.queryByText("Enfant")).not.toBeInTheDocument();
    expect(screen.queryByText("Petit-enfant")).not.toBeInTheDocument();
  });
});
