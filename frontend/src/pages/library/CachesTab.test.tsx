import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../../context";
import { CachesTab, type CacheRow } from "./CachesTab";

const api = vi.fn();
vi.mock("../../api", async (orig) => ({ ...(await orig<typeof import("../../api")>()), api: (...a: unknown[]) => api(...a) }));

const row = (over: Partial<CacheRow>): CacheRow => ({
  id: 1,
  key: "inventaire-serveurs",
  environment: "test",
  version: 3,
  size_bytes: 2048,
  expires_at: null,
  expired: false,
  sensitive: false,
  written_by_workflow: "inventaire",
  written_by_run: null,
  updated_at: "2026-11-23T10:00:00Z",
  ...over,
});
const ROWS = [row({}), row({ id: 2, key: "rapport", environment: "prod", version: 1, size_bytes: 12, expires_at: "2026-11-01T00:00:00Z", expired: true })];

function show(can = true) {
  api.mockImplementation(async (path: string, opts?: { method?: string }) => {
    if (opts?.method === "DELETE") return undefined;
    if (path.endsWith("/caches/1/")) return { ...ROWS[0], value: { serveurs: ["a", "b"] } };
    if (path.endsWith("/caches/")) return { count: ROWS.length, results: ROWS };
    return {};
  });
  const ctx = { workspace: { slug: "team-a" }, can: () => can } as unknown as Ctx;
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}>
        <MemoryRouter>
          <CachesTab />
        </MemoryRouter>
      </AppContext.Provider>
    </QueryClientProvider>,
  );
}

describe("Cache de l'espace", () => {
  beforeEach(() => {
    api.mockReset();
  });

  it("liste les entrées avec environnement, version, taille et état d'expiration", async () => {
    show();
    const table = await screen.findByRole("table", { name: "Entrées du cache" });
    expect(await within(table).findByText("inventaire-serveurs")).toBeInTheDocument();
    expect(within(table).getByText("2.0 Ko")).toBeInTheDocument();
    expect(within(table).getByText("Expiré")).toBeInTheDocument();
    expect(within(table).getByText("Jamais")).toBeInTheDocument();
    expect(within(table).getByText("Production")).toBeInTheDocument();
  });

  it("filtre par clé et par environnement", async () => {
    show();
    await screen.findByText("rapport");
    await userEvent.type(screen.getByLabelText("Rechercher une clé"), "rapp");
    expect(screen.queryByText("inventaire-serveurs")).toBeNull();
    await userEvent.clear(screen.getByLabelText("Rechercher une clé"));
    await userEvent.selectOptions(screen.getByLabelText("Environnement"), "test");
    expect(screen.queryByText("rapport")).toBeNull();
    expect(screen.getByText("inventaire-serveurs")).toBeInTheDocument();
  });

  it("« Voir » charge la valeur à la demande, la liste n'en contient pas", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Voir la valeur de inventaire-serveurs" }));
    expect(await screen.findByLabelText("Valeur du cache")).toHaveTextContent('"serveurs"');
    expect(api.mock.calls.filter((c) => String(c[0]).endsWith("/caches/1/"))).toHaveLength(1);
  });

  it("« Invalider » demande confirmation puis supprime l'entrée choisie seulement", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Invalider rapport (Production)" }));
    expect(api.mock.calls.some((c) => (c[1] as { method?: string } | undefined)?.method === "DELETE")).toBe(false);
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Invalider" }));
    await waitFor(() => expect(api.mock.calls.some((c) => (c[1] as { method?: string } | undefined)?.method === "DELETE")).toBe(true));
    const del = api.mock.calls.find((c) => (c[1] as { method?: string } | undefined)?.method === "DELETE");
    expect(String(del?.[0])).toBe("/workspaces/team-a/caches/2/");
  });

  it("une entrée sensible est signalée et sa valeur masquée pour qui n'est pas administrateur", async () => {
    const secret = row({ id: 3, key: "donnee-rh", sensitive: true });
    api.mockImplementation(async (path: string) => {
      if (path.endsWith("/caches/3/")) return { ...secret, value: null, masked: true };
      return { count: 1, results: [secret] };
    });
    const ctx = { workspace: { slug: "team-a" }, can: () => true } as unknown as Ctx;
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AppContext.Provider value={ctx}>
          <MemoryRouter>
            <CachesTab />
          </MemoryRouter>
        </AppContext.Provider>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Sensible")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Voir la valeur de donnee-rh" }));
    expect(await screen.findByText(/seuls les administrateurs de l'espace peuvent la voir/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Valeur du cache")).toBeNull();
  });

  it("pour un workflow : la liste est demandée pour sa clé exacte, sans recherche, avec son titre et un message vide adapté", async () => {
    api.mockImplementation(async () => ({ count: 0, results: [] }));
    const ctx = { workspace: { slug: "team-a" }, can: () => true } as unknown as Ctx;
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AppContext.Provider value={ctx}>
          <MemoryRouter>
            <CachesTab exactKey="inventaire serveurs" />
          </MemoryRouter>
        </AppContext.Provider>
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/Rien dans le cache pour l'instant/)).toBeInTheDocument();
    expect(api).toHaveBeenCalledWith("/workspaces/team-a/caches/?key=inventaire%20serveurs");
    expect(screen.getByText("Cache de ce workflow : inventaire serveurs")).toBeInTheDocument();
    expect(screen.queryByLabelText("Rechercher une clé")).toBeNull();
  });

  it("sans le droit de conception, aucun appel n'est fait et la raison est donnée", () => {
    show(false);
    expect(screen.getByText(/réservé aux personnes qui conçoivent/)).toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
  });
});
