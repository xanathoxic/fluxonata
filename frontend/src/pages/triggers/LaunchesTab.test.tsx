import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../../context";
import { LaunchesTab, type Launch } from "./LaunchesTab";

const api = vi.fn();
vi.mock("../../api", async (orig) => ({ ...(await orig<typeof import("../../api")>()), api: (...a: unknown[]) => api(...a) }));

const row = (over: Partial<Launch>): Launch => ({
  id: "11111111-1111-1111-1111-111111111111",
  workflow: "rapport",
  workflow_name: "Rapport mensuel",
  version: "latest",
  environment: "test",
  run_at: "2026-12-01T07:30:00Z",
  timezone: "Europe/Paris",
  status: "pending",
  note: "",
  requested_by: "alice",
  source_run: null,
  source_workflow: "planificateur",
  fired_run: null,
  fired_at: null,
  ...over,
});
const ROWS = [
  row({}),
  row({ id: "22222222-2222-2222-2222-222222222222", workflow_name: "Sauvegarde", status: "fired", fired_run: "run-9", run_at: "2026-11-20T07:30:00Z" }),
  row({
    id: "33333333-3333-3333-3333-333333333333",
    workflow_name: "Archive",
    status: "refused",
    note: "La personne qui avait programmé ce lancement n'a plus le droit de lancer ce workflow.",
    run_at: "2026-11-19T07:30:00Z",
  }),
];

function show(can = true) {
  api.mockImplementation(async (_path: string, opts?: { method?: string }) => {
    if (opts?.method === "DELETE") return undefined;
    return { count: ROWS.length, results: ROWS };
  });
  const ctx = { workspace: { slug: "team-a" }, can: () => can } as unknown as Ctx;
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}>
        <MemoryRouter>
          <LaunchesTab />
        </MemoryRouter>
      </AppContext.Provider>
    </QueryClientProvider>,
  );
}

describe("Lancements programmés", () => {
  beforeEach(() => {
    api.mockReset();
  });

  it("liste ce qui attend, ce qui est parti et ce qui a été refusé, avec le motif", async () => {
    show();
    await screen.findByText("Rapport mensuel");
    const table = screen.getByRole("table", { name: "Lancements programmés" });
    expect(within(table).getByText("Rapport mensuel")).toBeInTheDocument();
    expect(within(table).getAllByText("par planificateur")).toHaveLength(3);
    expect(within(table).getByText("Programmé")).toBeInTheDocument();
    expect(within(table).getByText("Lancé")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "Voir l'exécution" })).toHaveAttribute("href", "/executions/run-9");
    expect(within(table).getByText("Refusé")).toBeInTheDocument();
    expect(within(table).getByText(/n'a plus le droit de lancer/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("3 lancement(s)");
  });

  it("filtre par état", async () => {
    show();
    await screen.findByText("Rapport mensuel");
    await userEvent.selectOptions(screen.getByLabelText("État"), "refused");
    expect(screen.getByRole("status")).toHaveTextContent("1 lancement(s)");
    expect(screen.queryByText("Rapport mensuel")).toBeNull();
  });

  it("n'offre l'annulation que pour ce qui attend, et la confirme avant d'agir", async () => {
    show();
    const user = userEvent.setup();
    const cancels = await screen.findAllByRole("button", { name: /Annuler le lancement de/ });
    expect(cancels).toHaveLength(1); // le lancé et le refusé ne s'annulent plus
    const [first] = cancels;
    if (!first) throw new Error("bouton d'annulation absent");
    await user.click(first);
    expect(api).not.toHaveBeenCalledWith(expect.stringContaining("/scheduled-launches/1111"), expect.anything());
    await user.click(screen.getByRole("button", { name: "Annuler le lancement" }));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        "/workspaces/team-a/scheduled-launches/11111111-1111-1111-1111-111111111111/",
        expect.objectContaining({ method: "DELETE" }),
      ),
    );
  });

  it("ne propose pas d'annuler sans le droit de lancer", async () => {
    show(false);
    await screen.findByText("Rapport mensuel");
    expect(screen.queryByRole("button", { name: /Annuler le lancement de/ })).toBeNull();
  });

  it("explique le vide", async () => {
    api.mockResolvedValue({ count: 0, results: [] });
    const ctx = { workspace: { slug: "team-a" }, can: () => true } as unknown as Ctx;
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AppContext.Provider value={ctx}>
          <MemoryRouter>
            <LaunchesTab />
          </MemoryRouter>
        </AppContext.Provider>
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/ajoutez une étape « Programmer »/)).toBeInTheDocument();
  });
});
