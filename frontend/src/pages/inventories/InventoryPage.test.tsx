import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../../context";
import { InventoriesPage } from "./InventoriesPage";
import { InventoryPage, renderedCounts } from "./InventoryPage";
import type { InvDoc } from "./inventory";

const api = vi.fn();
vi.mock("../../api", async (orig) => ({ ...(await orig<typeof import("../../api")>()), api: (...a: unknown[]) => api(...a) }));

// absent de jsdom : l'arbre de dossiers s'adapte à la largeur de l'écran
window.matchMedia = ((query: string) => ({
  matches: false,
  media: query,
  addEventListener: () => undefined,
  removeEventListener: () => undefined,
})) as unknown as typeof window.matchMedia;

const DOC: InvDoc = {
  slug: "prod",
  name: "Production",
  description: "Serveurs",
  variables: { env: "prod" },
  groups: [
    { name: "web", variables: {}, children: [] },
    { name: "db", variables: {}, children: [] },
  ],
  hosts: [
    { name: "web1", variables: { ansible_host: "10.0.0.5" }, enabled: true, groups: ["web"] },
    { name: "db1", variables: {}, enabled: false, groups: ["db"] },
  ],
};
const puts = () => api.mock.calls.filter((c) => (c[1] as { method?: string } | undefined)?.method === "PUT");

function serve() {
  api.mockImplementation(async (path: string, opts?: { method?: string; json?: unknown }) => {
    if (opts?.method === "PUT") return opts.json;
    if (opts?.method === "DELETE") return {};
    if (path.endsWith("/inventories/"))
      return opts?.json
        ? { ...DOC, ...(opts.json as object) }
        : [{ slug: "prod", name: "Production", description: "Serveurs", folder: "", hosts: 2, groups: 2, updated_at: "2026-11-25T10:00:00Z" }];
    if (path.endsWith("/inventories/import/"))
      return {
        variables: {},
        groups: [{ name: "imp", variables: {}, children: [] }],
        hosts: [{ name: "imp1", variables: {}, enabled: true, groups: ["imp"] }],
      };
    if (path.endsWith("/rendered/")) return { all: { hosts: { web1: {} } } };
    if (/\/inventories\/[^/]+\/$/.test(path)) return DOC; // aussi l'inventaire qu'on vient de créer : la page y navigue
    return {};
  });
}

function show(ui: "list" | "page", can = true) {
  const ctx = { workspace: { slug: "team-a" }, can: () => can } as unknown as Ctx;
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}>
        <MemoryRouter initialEntries={[ui === "list" ? "/library/inventories" : "/library/inventories/prod"]}>
          <Routes>
            <Route path="/library/inventories" element={<InventoriesPage />} />
            <Route path="/library/inventories/:slug" element={<InventoryPage />} />
          </Routes>
        </MemoryRouter>
      </AppContext.Provider>
    </QueryClientProvider>,
  );
}

describe("Inventaires : liste", () => {
  beforeEach(() => {
    api.mockReset();
    serve();
  });
  it("liste les inventaires avec leurs effectifs et le lien vers l'éditeur", async () => {
    show("list");
    expect(await screen.findByRole("link", { name: "Production" })).toHaveAttribute("href", "/library/inventories/prod");
    expect(screen.getByText("prod")).toBeInTheDocument();
  });
  it("propose de créer un inventaire, avec l'identifiant déduit du nom et refus d'un doublon", async () => {
    show("list");
    await userEvent.click(await screen.findByRole("button", { name: /Nouvel inventaire/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Nom de l'inventaire"), "Prod");
    expect(within(dialog).getByText(/déjà utilisé/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Créer" })).toBeDisabled();
    await userEvent.clear(within(dialog).getByLabelText("Nom de l'inventaire"));
    await userEvent.type(within(dialog).getByLabelText("Nom de l'inventaire"), "Recette");
    await userEvent.click(within(dialog).getByRole("button", { name: "Créer" }));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        "/workspaces/team-a/inventories/",
        expect.objectContaining({ json: { slug: "recette", name: "Recette", description: "", folder: "" } }),
      ),
    );
  });
  it("un lecteur ne voit ni création ni suppression", async () => {
    show("list", false);
    await screen.findByRole("link", { name: "Production" });
    expect(screen.queryByRole("button", { name: /Nouvel inventaire/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Supprimer/ })).toBeNull();
  });
});

describe("Inventaire : aperçu", () => {
  beforeEach(() => {
    api.mockReset();
    serve();
  });
  it("dit clairement qu'aucune machine n'est active", async () => {
    api.mockImplementation(async (path: string) => (path.endsWith("/rendered/") ? { all: { hosts: {} } } : path.endsWith("/inventories/prod/") ? DOC : {}));
    show("page");
    await userEvent.click(await screen.findByRole("button", { name: "Aperçu Ansible" }));
    expect(await screen.findByRole("note")).toHaveTextContent(/Aucune machine active/);
    expect(screen.getByText("machine active")).toBeInTheDocument(); // zéro : singulier, comme en français
  });
  it("compte les machines sans doublon entre groupes et racine", () => {
    expect(renderedCounts({ all: { hosts: { a: {} }, vars: { x: 1 }, children: { g1: { hosts: { b: {}, a: {} } }, g2: { hosts: { b: {} } } } } })).toEqual({
      hosts: 2,
      groups: 2,
      vars: 1,
    });
    expect(renderedCounts(undefined)).toEqual({ hosts: 0, groups: 0, vars: 0 });
  });
});

describe("Inventaire : édition", () => {
  beforeEach(() => {
    api.mockReset();
    serve();
  });
  it("montre machines, groupes et variables, avec l'identifiant à donner à l'action", async () => {
    show("page");
    expect(await screen.findByRole("heading", { name: "Production" })).toBeInTheDocument();
    expect(screen.getByText(/inventory_ref: prod/)).toBeInTheDocument();
    expect(screen.getByLabelText("Nom de la machine 1")).toHaveValue("web1");
    expect(screen.getByLabelText("Variables de web1 (JSON)")).toHaveValue('{"ansible_host":"10.0.0.5"}');
    expect(screen.getByLabelText("Machine db1 active")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Enregistrer" })).toBeDisabled(); // rien n'a changé
  });
  it("ajoute une machine puis enregistre le document complet", async () => {
    show("page");
    await userEvent.click(await screen.findByRole("button", { name: /Ajouter une machine/ }));
    expect(await screen.findByText("Modifications non enregistrées.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    const sent = puts()[0]?.[1].json as InvDoc;
    expect(sent.hosts.map((h) => h.name)).toEqual(["web1", "db1", "machine1"]);
    expect(sent.hosts[2]).toEqual({ name: "machine1", variables: {}, enabled: true, groups: [] });
  });
  it("garde un JSON invalide visible et n'en envoie rien", async () => {
    show("page");
    const field = await screen.findByLabelText("Variables de web1 (JSON)");
    await userEvent.clear(field);
    await userEvent.type(field, "{{oups");
    expect(await screen.findByText("JSON invalide.")).toBeInTheDocument();
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(puts()).toHaveLength(0);
  });
  it("refuse d'enregistrer un nom de groupe avec un tiret et dit pourquoi", async () => {
    show("page");
    const name = await screen.findByLabelText("Nom du groupe 1");
    await userEvent.clear(name);
    await userEvent.type(name, "web-1");
    await userEvent.tab();
    expect(await screen.findByRole("alert")).toHaveTextContent(/sans tiret ni espace/);
    expect(screen.getByRole("button", { name: "Enregistrer" })).toBeDisabled();
  });
  it("supprimer un groupe le retire des machines, qui restent", async () => {
    show("page");
    await userEvent.click(await screen.findByRole("button", { name: "Supprimer le groupe web" }));
    await userEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    const sent = puts()[0]?.[1].json as InvDoc;
    expect(sent.groups.map((g) => g.name)).toEqual(["db"]);
    expect(sent.hosts.find((h) => h.name === "web1")?.groups).toEqual([]);
  });
  it("importe un inventaire YAML/JSON en remplaçant l'édition, sans rien enregistrer", async () => {
    show("page");
    await userEvent.click(await screen.findByRole("button", { name: "Importer YAML/JSON" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByLabelText("Inventaire YAML ou JSON"));
    await userEvent.paste("all: {}");
    await userEvent.click(within(dialog).getByRole("button", { name: "Importer" }));
    expect(await screen.findByLabelText("Nom de la machine 1")).toHaveValue("imp1");
    expect(puts()).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Enregistrer" })).toBeEnabled();
  });
  it("un lecteur consulte sans pouvoir modifier", async () => {
    show("page", false);
    await screen.findByRole("heading", { name: "Production" });
    expect(screen.queryByRole("button", { name: "Enregistrer" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Ajouter/ })).toBeNull();
    expect(screen.getByLabelText("Nom de la machine 1")).toBeDisabled();
  });
  it("l'aperçu Ansible montre la version enregistrée", async () => {
    show("page");
    await userEvent.click(await screen.findByRole("button", { name: "Aperçu Ansible" }));
    expect(await screen.findByRole("region", { name: "Inventaire au format JSON" })).toHaveTextContent('"web1"');
    expect(screen.getByText("machine active")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copier" })).toBeInTheDocument();
    expect(screen.queryByRole("note")).toBeNull(); // il y a une machine active : pas d'avertissement
  });
});
