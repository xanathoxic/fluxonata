import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../../context";
import { PackagesTab } from "./PackagesTab";

const api = vi.fn();
const apiAll = vi.fn();
vi.mock("../../api", async (orig) => ({
  ...(await orig<typeof import("../../api")>()),
  api: (...a: unknown[]) => api(...a),
  apiAll: (...a: unknown[]) => apiAll(...a),
}));

const action = (n: number) => ({ slug: `act-${n}`, name: `Action ${n}`, latest_version: { version: "1.0.0" } });

describe("Paquets : export", () => {
  beforeEach(() => {
    api.mockReset();
    apiAll.mockReset();
    api.mockResolvedValue({ results: [], count: 0, next: null });
  });

  it("propose TOUTES les actions de l'espace, pas seulement la première page de 50", async () => {
    apiAll.mockImplementation(async (path: string) => {
      if (path.includes("/actions/")) return { results: Array.from({ length: 70 }, (_, i) => action(i + 1)), count: 70, next: null };
      return { results: [], count: 0, next: null };
    });
    const ctx = { workspace: { slug: "team-a" }, can: () => true } as unknown as Ctx;
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AppContext.Provider value={ctx}>
          <MemoryRouter>
            <PackagesTab />
          </MemoryRouter>
        </AppContext.Provider>
      </QueryClientProvider>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Exporter un paquet" }));
    const group = await screen.findByRole("group", { name: "Actions à inclure" });
    expect(await within(group).findByText("0 sélectionné(s) sur 70")).toBeInTheDocument();
    expect(within(group).getByLabelText("Action 70 (v1.0.0)")).toBeInTheDocument();
    expect(apiAll).toHaveBeenCalledWith("/workspaces/team-a/actions/"); // lecture complète (par pages), et non la première page de 50
  });
});
