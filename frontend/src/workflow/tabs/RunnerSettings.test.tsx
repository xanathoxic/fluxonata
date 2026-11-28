import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WorkflowDetail } from "../../api";
import { AppContext, type Ctx } from "../../context";
import { emptyGraph } from "../graphOps";
import { SummaryTab } from "./SummaryTab";

const api = vi.fn();
vi.mock("../../api", async (orig) => ({ ...(await orig<typeof import("../../api")>()), api: (...a: unknown[]) => api(...a) }));

const CHOICES = [
  { id: "r1", name: "fabric-oob", description: "", scope: "dedicated", tags: ["linux"], runtimes: ["python", "shell"], status: "online" },
  { id: "r2", name: "partage-1", description: "", scope: "shared", tags: [], runtimes: ["python"], status: "paused" },
];
let wf: Partial<WorkflowDetail> = {};
// objets STABLES d'un rendu à l'autre : le vrai contexte les garde, et l'onglet relit ces valeurs dans un effet
const BASE = { description: "", folder: "", tags: [] as string[], updated_at: "2026-11-16T10:00:00Z", latest_version: null };
const GRAPH = emptyGraph();
const REFETCH = vi.fn();
vi.mock("../DraftContext", () => ({ useDraft: () => ({ slug: "parent", wf: { ...BASE, ...wf }, graph: GRAPH, refetchWorkflow: REFETCH }) }));

function show(can = true) {
  api.mockImplementation(async (path: string, opts?: { method?: string; json?: unknown }) => {
    if (opts?.method === "PATCH") return {};
    if (path.endsWith("/runner-choices/")) return { results: CHOICES };
    return { results: [], count: 0, next: null };
  });
  const ctx = { workspace: { slug: "team-a" }, can: () => can } as unknown as Ctx;
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}>
        <MemoryRouter>
          <SummaryTab />
        </MemoryRouter>
      </AppContext.Provider>
    </QueryClientProvider>,
  );
}
const openEnvironment = () => userEvent.click(screen.getByRole("tab", { name: "Environnement" })); // les réglages du runner sont dans le sous-onglet « Environnement »
const sent = () => {
  const call = api.mock.calls.find((c) => (c[1] as { method?: string } | undefined)?.method === "PATCH");
  if (!call) throw new Error("aucun enregistrement envoyé");
  return (call[1] as { json: Record<string, unknown> }).json;
};

describe("Réglages d'un workflow : runner imposé", () => {
  beforeEach(() => {
    api.mockReset();
    wf = {};
  });

  it("propose les runners utilisables (état, dédié ou partagé) et le choix automatique par défaut", async () => {
    show();
    await openEnvironment();
    const select = await screen.findByLabelText("Runner imposé");
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(3));
    expect(select).toHaveValue("");
    expect(within(select).getByRole("option", { name: "fabric-oob — dédié, en ligne" })).toBeInTheDocument();
    expect(within(select).getByRole("option", { name: "partage-1 — partagé, en pause" })).toBeInTheDocument();
    expect(screen.getByLabelText("Imposer ce runner aux sous-workflows")).toBeDisabled(); // sans runner, rien à imposer
  });

  it("enregistre le runner choisi et l'extension aux sous-workflows", async () => {
    show();
    await openEnvironment();
    const select = await screen.findByLabelText("Runner imposé");
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(3));
    await userEvent.selectOptions(select, "r1");
    const force = screen.getByLabelText("Imposer ce runner aux sous-workflows");
    expect(force).toBeEnabled();
    expect(screen.getByText(/Langages : python, shell · étiquettes : linux/)).toBeInTheDocument();
    await userEvent.click(force);
    await userEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(api.mock.calls.some((c) => (c[1] as { method?: string } | undefined)?.method === "PATCH")).toBe(true));
    expect(sent()).toMatchObject({ runner: "r1", runner_force_children: true });
  });

  it("retirer le runner décoche et n'envoie jamais l'extension aux sous-workflows", async () => {
    wf = { runner: "r1", runner_name: "fabric-oob", runner_force_children: true };
    show();
    await openEnvironment();
    const select = await screen.findByLabelText("Runner imposé");
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(3));
    expect(select).toHaveValue("r1");
    expect(screen.getByLabelText("Imposer ce runner aux sous-workflows")).toBeChecked();
    await userEvent.selectOptions(select, "");
    expect(screen.getByLabelText("Imposer ce runner aux sous-workflows")).not.toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(api.mock.calls.some((c) => (c[1] as { method?: string } | undefined)?.method === "PATCH")).toBe(true));
    expect(sent()).toMatchObject({ runner: "", runner_force_children: false });
  });

  it("l'exécution séquentielle est désactivée par défaut (mode concurrent) et s'active avec le commutateur", async () => {
    show();
    await openEnvironment();
    const mode = await screen.findByLabelText("Exécution séquentielle");
    expect(mode).not.toBeChecked();
    await userEvent.click(mode);
    expect(mode).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(api.mock.calls.some((c) => (c[1] as { method?: string } | undefined)?.method === "PATCH")).toBe(true));
    expect(sent()).toMatchObject({ run_mode: "sequential" });
  });

  it("un workflow déjà séquentiel est affiché activé, et le désactiver renvoie le mode concurrent", async () => {
    wf = { run_mode: "sequential" };
    show();
    await openEnvironment();
    const mode = await screen.findByLabelText("Exécution séquentielle");
    expect(mode).toBeChecked();
    await userEvent.click(mode);
    await userEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(api.mock.calls.some((c) => (c[1] as { method?: string } | undefined)?.method === "PATCH")).toBe(true));
    expect(sent()).toMatchObject({ run_mode: "concurrent" });
  });

  it("la publication dans le cache est désactivée par défaut ; une clé et une durée de vie sont enregistrées", async () => {
    show();
    await openEnvironment();
    const key = await screen.findByLabelText("Publier le résultat dans le cache (clé)");
    expect(key).toHaveValue("");
    expect(screen.queryByLabelText("Durée de vie du résultat (secondes, facultatif)")).toBeNull();
    await userEvent.type(key, "inventaire-serveurs");
    await userEvent.type(screen.getByLabelText("Durée de vie du résultat (secondes, facultatif)"), "21600");
    await userEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(api.mock.calls.some((c) => (c[1] as { method?: string } | undefined)?.method === "PATCH")).toBe(true));
    expect(sent()).toMatchObject({ cache_key: "inventaire-serveurs", cache_ttl_seconds: 21600 });
  });

  it("le résultat publié peut être marqué sensible (case visible seulement avec une clé)", async () => {
    show();
    await openEnvironment();
    expect(screen.queryByLabelText("Résultat sensible")).toBeNull();
    await userEvent.type(await screen.findByLabelText("Publier le résultat dans le cache (clé)"), "inv");
    await userEvent.click(screen.getByLabelText("Résultat sensible"));
    await userEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(api.mock.calls.some((c) => (c[1] as { method?: string } | undefined)?.method === "PATCH")).toBe(true));
    expect(sent()).toMatchObject({ cache_key: "inv", cache_sensitive: true });
  });

  it("la page Informations a trois sous-onglets ; exécution et cache sont dans « Environnement »", async () => {
    show();
    expect(screen.getByRole("tab", { name: "Informations", selected: true })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Enchaînements" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Runner imposé")).toBeNull(); // pas dans l'onglet par défaut
    await openEnvironment();
    expect(await screen.findByLabelText("Runner imposé")).toBeInTheDocument();
    expect(screen.getByLabelText("Publier le résultat dans le cache (clé)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Enchaînements" }));
    expect(screen.getByLabelText(/Workflow d'erreur/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Runner imposé")).toBeNull();
    await userEvent.click(screen.getByRole("tab", { name: "Informations" }));
    expect(screen.getByLabelText("Description")).toBeInTheDocument();
    expect(screen.getByLabelText(/Exposer comme outil MCP/)).toBeInTheDocument(); // l'intégration est dans l'onglet Informations
  });

  it("une publication existante est affichée, et vider la clé la désactive (durée de vie remise à vide)", async () => {
    wf = { cache_key: "inv", cache_ttl_seconds: 60 };
    show();
    await openEnvironment();
    const key = await screen.findByLabelText("Publier le résultat dans le cache (clé)");
    expect(key).toHaveValue("inv");
    expect(screen.getByLabelText("Durée de vie du résultat (secondes, facultatif)")).toHaveValue("60");
    await userEvent.clear(screen.getByLabelText("Durée de vie du résultat (secondes, facultatif)"));
    await userEvent.clear(key);
    await userEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(api.mock.calls.some((c) => (c[1] as { method?: string } | undefined)?.method === "PATCH")).toBe(true));
    expect(sent()).toMatchObject({ cache_key: "", cache_ttl_seconds: null });
  });

  it("un runner imposé qui n'est plus utilisable par l'espace est montré tel quel, avec une alerte", async () => {
    wf = { runner: "disparu", runner_name: "ancien-runner", runner_force_children: false };
    show();
    await openEnvironment();
    const select = await screen.findByLabelText("Runner imposé");
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(4));
    expect(select).toHaveValue("disparu");
    expect(within(select).getByRole("option", { name: "ancien-runner (non disponible pour cet espace)" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("n'est plus utilisable par cet espace");
  });

  it("sans le droit de modifier, le choix est en lecture seule", async () => {
    show(false);
    await openEnvironment();
    expect(await screen.findByLabelText("Runner imposé")).toBeDisabled();
  });
});
