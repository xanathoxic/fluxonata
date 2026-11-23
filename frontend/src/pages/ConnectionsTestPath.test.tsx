import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { ConnectionsPage } from "./ConnectionsPage";

const api = vi.fn();
vi.mock("../api", async (orig) => ({ ...(await orig<typeof import("../api")>()), api: (...a: unknown[]) => api(...a) }));

const base = { environment: null, last_test_ok: null, last_test_at: null, last_test_message: "", secret: "s", secret_provider: "internal" };
const CONNS = [
  { ...base, id: 1, name: "gitlab", label: "GitLab", kind: "http_bearer", config: { base_url: "https://git.exemple.org/api/v4/" } },
  {
    ...base,
    id: 2,
    name: "keycloak",
    label: "Keycloak",
    kind: "oauth2_client",
    config: {
      base_url: "https://sso.exemple.org/admin",
      token_url: "https://sso.exemple.org/realms/master/protocol/openid-connect/token",
      client_id: "fluxonata-admin",
    },
  },
  { ...base, id: 3, name: "db", label: "Base", kind: "postgres", config: { host: "db", database: "d", username: "u" } },
];

function show() {
  api.mockImplementation(async (path: string, opts?: { method?: string; json?: unknown }) => {
    if (opts?.method === "PATCH") return { ...CONNS[0], ...(opts.json as object) };
    if (path.endsWith("/connections/")) return { results: CONNS };
    if (path.endsWith("/secrets/")) return { results: [{ name: "s", provider: "internal", used_by: 1 }] };
    if (path.endsWith("/integrations/")) return { results: [] };
    return {};
  });
  const ctx = { workspace: { slug: "team-a" }, can: () => true } as unknown as Ctx;
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}>
        <MemoryRouter>
          <ConnectionsPage />
        </MemoryRouter>
      </AppContext.Provider>
    </QueryClientProvider>,
  );
}

describe("Connexions : chemin de test", () => {
  beforeEach(() => {
    api.mockReset();
  });

  it("un service HTTP propose un chemin de test facultatif, envoyé à l'enregistrement", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Modifier la connexion GitLab" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Chemin de test/), "/user");
    await userEvent.click(within(dialog).getByRole("button", { name: /Enregistrer/ }));
    const call = api.mock.calls.find((c) => (c[1] as { method?: string } | undefined)?.method === "PATCH");
    if (!call) throw new Error("aucune modification envoyée");
    expect((call[1] as { json: { config: Record<string, string> } }).json.config).toEqual({
      base_url: "https://git.exemple.org/api/v4/",
      test_path: "/user",
    });
  });

  it("un service OAuth2 (identifiants client) propose aussi un chemin de test, conservé à l'enregistrement", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Modifier la connexion Keycloak" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Chemin de test/), "/realms");
    await userEvent.click(within(dialog).getByRole("button", { name: /Enregistrer/ }));
    const call = api.mock.calls.find((c) => (c[1] as { method?: string } | undefined)?.method === "PATCH");
    if (!call) throw new Error("aucune modification envoyée");
    expect((call[1] as { json: { config: Record<string, string> } }).json.config).toMatchObject({ client_id: "fluxonata-admin", test_path: "/realms" });
  });

  it("un type sans adresse HTTP (PostgreSQL) n'a pas ce champ", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Modifier la connexion Base" }));
    expect(within(await screen.findByRole("dialog")).queryByLabelText(/Chemin de test/)).toBeNull();
  });
});
