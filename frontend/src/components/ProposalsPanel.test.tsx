import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { ProposalsPanel } from "./ProposalsPanel";

const apiAll = vi.fn();
const api = vi.fn();
vi.mock("../api", async (orig) => ({ ...(await orig<typeof import("../api")>()), apiAll: (...a: unknown[]) => apiAll(...a), api: (...a: unknown[]) => api(...a) }));

const rows = [
  { slug: "nettoyer", name: "Nettoyer un texte", description: "Retire les espaces", status: "proposed", latest_version: { version: "1.0.0" } },
  { slug: "deja-active", name: "Déjà active", description: "", status: "active", latest_version: null }, // jamais présentée comme une proposition
];

function show(perms: string[], kind: "action" | "skill" = "action") {
  const ctx = { workspace: { slug: "team-a" }, can: (p: string) => perms.includes(p) } as unknown as Ctx;
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}><MemoryRouter><ProposalsPanel kind={kind} /></MemoryRouter></AppContext.Provider>
    </QueryClientProvider>,
  );
}

describe("propositions d'un assistant", () => {
  beforeEach(() => { apiAll.mockReset().mockResolvedValue({ results: rows, count: 2, next: null }); api.mockReset().mockResolvedValue({}); });

  it("liste seulement les propositions, avec Relire / Activer / Rejeter", async () => {
    show(["view", "edit", "publish"]);
    expect(await screen.findByRole("heading", { name: "Propositions d'un assistant (1)" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Nettoyer un texte" })).toHaveAttribute("href", "/library/actions/nettoyer");
    expect(screen.queryByText("Déjà active")).not.toBeInTheDocument();
    expect(apiAll).toHaveBeenCalledWith("/workspaces/team-a/actions/?status=proposed");
    expect(screen.getByRole("button", { name: "Activer Nettoyer un texte après relecture" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rejeter Nettoyer un texte" })).toBeInTheDocument();
  });

  it("Activer appelle l'activation du serveur, Rejeter déprécie", async () => {
    show(["view", "edit", "publish"]);
    await userEvent.click(await screen.findByRole("button", { name: "Activer Nettoyer un texte après relecture" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/workspaces/team-a/actions/nettoyer/activate/", expect.objectContaining({ method: "POST" })));
    await userEvent.click(screen.getByRole("button", { name: "Rejeter Nettoyer un texte" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/workspaces/team-a/actions/nettoyer/deprecate/", expect.objectContaining({ method: "POST" })));
  });

  it("sans droit de publier : on peut relire, pas activer", async () => {
    show(["view", "edit"]);
    expect(await screen.findByRole("link", { name: "Relire Nettoyer un texte" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Activer|Rejeter/ })).not.toBeInTheDocument();
  });

  it("sans droit de modifier : rien n'est demandé ni affiché", async () => {
    show(["view"]);
    await new Promise((r) => setTimeout(r, 30));
    expect(apiAll).not.toHaveBeenCalled();
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });

  it("les skills utilisent leur propre route", async () => {
    show(["view", "edit", "publish"], "skill");
    expect(await screen.findByRole("link", { name: "Nettoyer un texte" })).toHaveAttribute("href", "/library/skills/nettoyer");
    expect(apiAll).toHaveBeenCalledWith("/workspaces/team-a/ai/skills/?status=proposed");
  });
});
