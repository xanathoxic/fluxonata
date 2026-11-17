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
  { ...base, id: 1, name: "netbox", label: "NetBox interne", kind: "http_bearer", config: { base_url: "https://netbox.local/", skip_tls_verify: "1" } },
  { ...base, id: 2, name: "api", label: "API publique", kind: "http_bearer", config: { base_url: "https://api.exemple.org/" } },
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
function patchBody(): Record<string, string> {
  const call = api.mock.calls.find((c) => (c[1] as { method?: string } | undefined)?.method === "PATCH");
  if (!call) throw new Error("aucune modification envoyée");
  return (call[1] as { json: { config: Record<string, string> } }).json.config;
}

describe("Connexions : ne pas vérifier le certificat du serveur", () => {
  beforeEach(() => {
    api.mockReset();
  });

  it("signale dans la liste les seules connexions dont la vérification est désactivée", async () => {
    show();
    await screen.findByText("NetBox interne");
    expect(screen.getAllByText("Certificat non vérifié")).toHaveLength(1);
    expect(within(screen.getByText("NetBox interne").closest("tr") as HTMLElement).getByText("Certificat non vérifié")).toBeInTheDocument();
  });

  it("la case reflète la valeur enregistrée et avertit quand elle est cochée", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Modifier la connexion NetBox interne" }));
    const box = within(await screen.findByRole("dialog")).getByLabelText("Ne pas vérifier le certificat du serveur");
    expect(box).toBeChecked();
    expect(screen.getByRole("alert")).toHaveTextContent("n'importe quel certificat est accepté");
  });

  it("décocher retire le réglage de la configuration envoyée (la vérification redevient la règle)", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Modifier la connexion NetBox interne" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByLabelText("Ne pas vérifier le certificat du serveur"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: /Enregistrer/ }));
    expect(patchBody()).toEqual({ base_url: "https://netbox.local/" });
  });

  it("cocher envoie « 1 » ; un type sans choix de certificat (PostgreSQL) n'a pas la case", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Modifier la connexion API publique" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Ne pas vérifier le certificat du serveur")).not.toBeChecked();
    await userEvent.click(within(dialog).getByLabelText("Ne pas vérifier le certificat du serveur"));
    await userEvent.click(within(dialog).getByRole("button", { name: /Enregistrer/ }));
    expect(patchBody()).toEqual({ base_url: "https://api.exemple.org/", skip_tls_verify: "1" });
    await userEvent.click(await screen.findByRole("button", { name: "Modifier la connexion Base" }));
    expect(within(await screen.findByRole("dialog")).queryByLabelText("Ne pas vérifier le certificat du serveur")).toBeNull();
  });
});
