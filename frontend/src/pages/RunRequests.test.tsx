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

const AUTO_OFF = { enabled: false, unrestricted: false, enabled_by: null, enabled_at: null, can_change: true };

function show(over: object = {}, can = true, auto: object = AUTO_OFF) {
  api.mockImplementation(async (path: string, opts?: { method?: string }) => {
    if (String(path).endsWith("/run-requests/auto/")) return opts?.method === "PUT" ? { ...AUTO_OFF, enabled: true } : auto;
    return opts?.method === "POST" ? {} : [{ ...REQ, ...over }];
  });
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
const puts = () => api.mock.calls.filter((c) => c[1]?.method === "PUT").map((c) => [String(c[0]), c[1]?.json]);

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

  describe("validation automatique", () => {
    const box = () => screen.findByRole("checkbox", { name: "Valider automatiquement les lancements demandés par un assistant" });

    it("est désactivée par défaut et n'envoie rien tant qu'on ne confirme pas l'avertissement", async () => {
      show();
      const user = userEvent.setup();
      expect(await box()).not.toBeChecked();
      await user.click(await box());
      const d = within(await screen.findByRole("dialog", { name: "Activer la validation automatique ?" }));
      expect(d.getByText(/plus aucune confirmation humaine/i)).toBeInTheDocument();
      expect(d.getByText(/production reste toujours refusée/i)).toBeInTheDocument();
      expect(puts()).toEqual([]);
      await user.click(d.getByRole("button", { name: "Annuler" }));
      expect(puts()).toEqual([]);
      expect(await box()).not.toBeChecked();
    });

    it("s'active après avoir confirmé l'avertissement", async () => {
      show();
      const user = userEvent.setup();
      await user.click(await box());
      await user.click(await screen.findByRole("button", { name: "Activer la validation automatique" }));
      expect(puts().map((p) => p[0])).toEqual(["/workspaces/team-a/run-requests/auto/"]);
      expect(puts()[0]?.[1]).toEqual({ enabled: true, unrestricted: false });
    });

    it("propose le débridage total, décoché par défaut, avec un avertissement renforcé quand on le coche", async () => {
      show();
      const user = userEvent.setup();
      await user.click(await box());
      const d = within(await screen.findByRole("dialog", { name: "Activer la validation automatique ?" }));
      const full = d.getByRole("checkbox", { name: "Débridage total" });
      expect(full).not.toBeChecked();
      expect(d.getByText(/publie lui-même/)).toBeInTheDocument();
      expect(d.getByText(/Reste interdit/)).toBeInTheDocument();
      expect(d.queryByText(/sans que personne ne l'ait lu/)).toBeNull();
      await user.click(full);
      expect(d.getByText(/sans que\s+personne ne l'ait lu|sans que personne ne l'ait lu/)).toBeInTheDocument();
      await user.click(d.getByRole("button", { name: "Activer avec débridage total" }));
      expect(puts()[0]?.[1]).toEqual({ enabled: true, unrestricted: true });
    });

    it("affiche le débridage total actif et permet de changer le niveau", async () => {
      show({}, true, { ...AUTO_OFF, enabled: true, unrestricted: true, enabled_by: "adele", enabled_at: "2026-11-28T09:00:00Z" });
      const user = userEvent.setup();
      expect(await screen.findByText(/Débridage total actif/)).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Changer le niveau d'autonomie" }));
      const d = within(await screen.findByRole("dialog", { name: "Niveau d'autonomie de l'assistant" }));
      expect(d.getByRole("checkbox", { name: "Débridage total" })).toBeChecked();
      await user.click(d.getByRole("checkbox", { name: "Débridage total" }));
      await user.click(d.getByRole("button", { name: "Activer la validation automatique" }));
      expect(puts()[0]?.[1]).toEqual({ enabled: true, unrestricted: false });
    });

    it("se désactive d'un clic, sans avertissement", async () => {
      show({}, true, { ...AUTO_OFF, enabled: true, enabled_by: "adele", enabled_at: "2026-11-27T09:00:00Z" });
      const user = userEvent.setup();
      expect(await screen.findByText(/Activée par adele/)).toBeInTheDocument();
      await user.click(await box());
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(puts()[0]?.[1]).toEqual({ enabled: false });
    });

    it("est inutilisable sans le droit d'administrer l'espace", async () => {
      show({}, true, { ...AUTO_OFF, can_change: false });
      expect(await box()).toBeDisabled();
      expect(screen.getByText(/Seul un administrateur de l'espace/)).toBeInTheDocument();
    });
  });
});
