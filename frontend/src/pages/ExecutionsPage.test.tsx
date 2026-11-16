import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { ExecutionsPage } from "./ExecutionsPage";

const apiAll = vi.fn();
vi.mock("../api", async (orig) => ({ ...(await orig<typeof import("../api")>()), apiAll: (...a: unknown[]) => apiAll(...a) }));

const run = (id: string, name: string, status: string) => ({ id, workflow: { name }, status, status_label: status, needs_decision: false, environment: "test", created_at: "2026-11-12T08:00:00Z" });
const RUNS = [run("1", "Redémarrer des VM", "succeeded"), run("2", "Sauvegarder", "failed")];

/** Le serveur applique les filtres (texte, statut) : sans correspondance il répond une liste vide, comme l'API réelle. */
function serve(all: ReturnType<typeof run>[]) {
  apiAll.mockImplementation(async (url?: string) => {
    if (!url?.includes("/workflow-runs/")) return { results: [], count: 0, next: null }; // aucune exécution d'action
    const qs = new URLSearchParams(url.split("?")[1] ?? "");
    const q = (qs.get("q") ?? "").toLowerCase();
    const status = qs.get("status");
    const results = all.filter((r) => (!q || r.workflow.name.toLowerCase().includes(q)) && (!status || r.status === status));
    return { results, count: results.length, next: null };
  });
}

function show() {
  const ctx = { workspace: { slug: "team-a" } } as unknown as Ctx;
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}><MemoryRouter><ExecutionsPage /></MemoryRouter></AppContext.Provider>
    </QueryClientProvider>,
  );
}

const NO_RUN_YET = /Aucune exécution pour l'instant/;
const NO_MATCH = "Aucune exécution ne correspond à ces filtres";

describe("Mes exécutions : les états de la liste", () => {
  beforeEach(() => apiAll.mockReset());

  it("aucune exécution et aucun filtre : message d'absence totale, pas de message de filtre", async () => {
    serve([]);
    show();
    expect(await screen.findByText(NO_RUN_YET)).toBeInTheDocument();
    expect(screen.queryByText(NO_MATCH)).not.toBeInTheDocument();
  });

  it("résultats présents : la liste s'affiche, aucun message d'absence", async () => {
    serve(RUNS);
    show();
    expect(await screen.findByText("Redémarrer des VM")).toBeInTheDocument();
    expect(screen.getByText("Sauvegarder")).toBeInTheDocument();
    expect(screen.queryByText(NO_RUN_YET)).not.toBeInTheDocument();
    expect(screen.queryByText(NO_MATCH)).not.toBeInTheDocument();
  });

  it("recherche sans résultat : message de filtre (et non d'absence totale), avec « Tout effacer »", async () => {
    serve(RUNS);
    show();
    await screen.findByText("Redémarrer des VM");
    await userEvent.type(screen.getByLabelText("Rechercher", { exact: true }), "zzz-introuvable");
    expect(await screen.findByText(NO_MATCH)).toBeInTheDocument();
    expect(screen.queryByText(NO_RUN_YET)).not.toBeInTheDocument();
    expect(screen.getByText("Texte : « zzz-introuvable »")).toBeInTheDocument();
  });

  it("statut sans résultat : même message de filtre", async () => {
    serve([run("1", "Redémarrer des VM", "succeeded")]);
    show();
    await screen.findByText("Redémarrer des VM");
    await userEvent.click(screen.getByRole("button", { name: /^Échouées/ }));
    expect(await screen.findByText(NO_MATCH)).toBeInTheDocument();
    expect(screen.queryByText(NO_RUN_YET)).not.toBeInTheDocument();
  });

  it("« Tout effacer » retire les filtres et rend la liste", async () => {
    serve(RUNS);
    show();
    await screen.findByText("Redémarrer des VM");
    await userEvent.type(screen.getByLabelText("Rechercher", { exact: true }), "zzz-introuvable");
    await screen.findByText(NO_MATCH);
    await userEvent.click(screen.getAllByRole("button", { name: "Tout effacer" })[0] as HTMLElement);
    expect(await screen.findByText("Redémarrer des VM")).toBeInTheDocument();
    expect(screen.queryByText(NO_MATCH)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Rechercher", { exact: true })).toHaveValue("");
  });
  it("erreur du serveur avec un filtre actif : l'alerte s'affiche, sans message de filtre ni d'absence", async () => {
    serve(RUNS);
    show();
    await screen.findByText("Redémarrer des VM");
    const ok = apiAll.getMockImplementation() as (url?: string) => Promise<unknown>;
    apiAll.mockImplementation(async (url?: string) => { if (url?.includes("q=vm")) throw new Error("panne"); return ok(url); });
    await userEvent.type(screen.getByLabelText("Rechercher", { exact: true }), "vm");
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(NO_MATCH)).not.toBeInTheDocument();
    expect(screen.queryByText(NO_RUN_YET)).not.toBeInTheDocument();
  });
});
