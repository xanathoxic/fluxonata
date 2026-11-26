import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { RunRequests } from "./RunRequests";

const api = vi.fn();
vi.mock("../api", async (orig) => ({ ...(await orig<typeof import("../api")>()), api: (...a: unknown[]) => api(...a) }));

const REQ = {
  id: "q-1",
  workflow: { slug: "salue", name: "Saluer", version: "1.0.0" },
  environment: "test",
  inputs: { nom: "Ada" },
  reason: "Vérifier le message",
  client: "claude",
  status: "pending",
  requested_by: "dana",
  created_at: "2026-11-26T10:00:00Z",
  expires_at: "2026-11-26T10:15:00Z",
  can_decide: true,
};

function show(over: object = {}, can = true) {
  api.mockImplementation(async (_path: string, opts?: { method?: string }) => (opts?.method === "POST" ? {} : [{ ...REQ, ...over }]));
  const ctx = { workspace: { slug: "team-a" }, can: () => can } as unknown as Ctx;
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}>
        <MemoryRouter>
          <RunRequests />
        </MemoryRouter>
      </AppContext.Provider>
    </QueryClientProvider>,
  );
}
const posts = () => api.mock.calls.filter((c) => c[1]?.method === "POST").map((c) => String(c[0]));

describe("Lancements demandés par un assistant", () => {
  beforeEach(() => api.mockReset());

  it("montre le workflow, l'environnement, le motif et les entrées avant toute décision", async () => {
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Examiner le lancement de Saluer" }));
    const d = within(await screen.findByRole("dialog"));
    expect(d.getByText("Vérifier le message")).toBeInTheDocument();
    expect(d.getByText("Ada")).toBeInTheDocument();
    expect(d.getAllByText("Recette").length).toBeGreaterThan(0);
    expect(posts()).toEqual([]); // ouvrir la fenêtre ne lance rien
  });

  it("ne lance qu'après le clic de confirmation", async () => {
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Examiner le lancement de Saluer" }));
    await user.click(await screen.findByRole("button", { name: "Confirmer et lancer en recette" }));
    expect(posts()).toEqual(["/workspaces/team-a/run-requests/q-1/confirm/"]);
  });

  it("permet de refuser sans rien lancer", async () => {
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Examiner le lancement de Saluer" }));
    await user.click(await screen.findByRole("button", { name: "Refuser" }));
    expect(posts()).toEqual(["/workspaces/team-a/run-requests/q-1/refuse/"]);
  });

  it("désactive la confirmation quand le rôle ne permet pas de lancer", async () => {
    show({ can_decide: false });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Examiner le lancement de Saluer" }));
    expect(await screen.findByRole("button", { name: "Confirmer et lancer en recette" })).toBeDisabled();
  });
});
