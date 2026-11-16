import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { ConnectionsPage } from "./ConnectionsPage";

const api = vi.fn();
vi.mock("../api", async (orig) => ({ ...(await orig<typeof import("../api")>()), api: (...a: unknown[]) => api(...a) }));

const CONNS = [
  { id: 7, name: "base", label: "Base clients", kind: "postgres", config: { host: "db.exemple.test", database: "clients", username: "lecteur" }, secret: "base-all", secret_provider: "internal", environment: null, last_test_ok: true, last_test_at: null, last_test_message: "ok" },
  { id: 8, name: "srv", label: "Serveur", kind: "ssh", config: { username: "robot" }, secret: "cle-srv", secret_provider: "internal", environment: null, last_test_ok: null, last_test_at: null, last_test_message: "" },
];
const SECRETS = [{ name: "base-all", provider: "internal", used_by: 1 }, { name: "partage", provider: "internal", used_by: 3 }, { name: "cle-srv", provider: "internal", used_by: 1 }];

function serve() {
  api.mockImplementation(async (path: string, opts?: { method?: string; json?: unknown }) => {
    if (opts?.method === "PATCH") return { ...CONNS[0], ...(opts.json as object) };
    if (path.endsWith("/connections/")) return { results: CONNS };
    if (path.endsWith("/secrets/")) return { results: SECRETS };
    if (path.endsWith("/integrations/")) return { results: [] };
    if (path.endsWith("/vault/")) return { available: false };
    return {};
  });
}

function show(can = true) {
  const ctx = { workspace: { slug: "team-a" }, can: () => can } as unknown as Ctx;
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}><MemoryRouter><ConnectionsPage /></MemoryRouter></AppContext.Provider>
    </QueryClientProvider>,
  );
}
const patches = () => api.mock.calls.filter((c) => (c[1] as { method?: string } | undefined)?.method === "PATCH");

describe("Connexions : modification", () => {
  beforeEach(() => { api.mockReset(); serve(); });

  it("opens the edit form prefilled, with the identifier, type and environment explained as fixed", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Modifier la connexion Base clients" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Nom lisible")).toHaveValue("Base clients");
    expect(within(dialog).getByLabelText(/Serveur$/)).toHaveValue("db.exemple.test");
    expect(within(dialog).getByText(/ne changent pas/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/Conserver le secret actuel \(base-all\)/)).toBeChecked();
  });

  it("saves the label and settings without touching the secret (nothing about a secret is sent)", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Modifier la connexion Base clients" }));
    const dialog = await screen.findByRole("dialog");
    const label = within(dialog).getByLabelText("Nom lisible");
    await userEvent.clear(label);
    await userEvent.type(label, "Clients (principale)");
    await userEvent.click(within(dialog).getByRole("button", { name: "Enregistrer les modifications" }));
    const [[path, opts]] = patches() as [[string, { json: Record<string, unknown> }]];
    expect(path).toBe("/workspaces/team-a/connections/7/");
    expect(opts.json).toMatchObject({ id: 7, label: "Clients (principale)", config: { host: "db.exemple.test", database: "clients", username: "lecteur" } });
    expect(Object.keys(opts.json).filter((k) => k.startsWith("secret") || k === "vault" || k === "new_secret_name")).toEqual([]);
  });

  it("switches to an existing secret, and cannot save until one is chosen", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Modifier la connexion Base clients" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByLabelText(/Choisir un secret existant/));
    expect(within(dialog).getByRole("button", { name: "Enregistrer les modifications" })).toBeDisabled();
    await userEvent.selectOptions(within(dialog).getByLabelText("Secret existant"), "partage");
    await userEvent.click(within(dialog).getByRole("button", { name: "Enregistrer les modifications" }));
    expect((patches()[0] as [string, { json: Record<string, unknown> }])[1].json).toMatchObject({ secret: "partage" });
  });

  it("creates a new secret from the edit form, optionally named, with the value only in that request", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Modifier la connexion Base clients" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByLabelText(/Créer un nouveau secret/));
    await userEvent.type(within(dialog).getByLabelText("Nom du nouveau secret (facultatif)"), "mdp-clients");
    await userEvent.type(within(dialog).getByLabelText(/Secret \(jeton, mot de passe…\)/), "valeur-synthetique-1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Enregistrer les modifications" }));
    expect((patches()[0] as [string, { json: Record<string, unknown> }])[1].json).toMatchObject({ secret_provider: "internal", secret_value: "valeur-synthetique-1", new_secret_name: "mdp-clients" });
  });

  it("for an SSH identity, the new secret is a multi-line field with the explicit instructions", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Modifier la connexion Serveur" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByLabelText(/Créer un nouveau secret/));
    const field = within(dialog).getByLabelText("Mot de passe OU clé privée du compte");
    expect(field.tagName).toBe("TEXTAREA");
    expect(within(dialog).getByText(/Sans serveur|impossible si vous avez laissé le serveur vide/)).toBeInTheDocument();
  });

  it("offers no edit button to someone who cannot manage connections", async () => {
    show(false);
    await screen.findByText("Base clients");
    expect(screen.queryByRole("button", { name: /Modifier la connexion/ })).toBeNull();
  });
});
