import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { DraftProvider, useDraft } from "./DraftContext";
import { addNode, defaultNode, emptyGraph } from "./graphOps";

/** Serveur simulé : refuse (409) toute écriture dont la révision n'est pas la courante, comme l'API réelle ; la réponse est lente. */
const server = { revision: 1, latency: 5000, inflight: 0, maxInflight: 0, puts: 0, conflicts: 0 };
const apiCall = vi.fn();
const apiAll = vi.fn(async () => ({ results: [], count: 0, next: null }));
vi.mock("../api", async (orig) => ({
  ...(await orig<typeof import("../api")>()),
  api: (...a: unknown[]) => apiCall(...a),
  apiAll: (...a: unknown[]) => apiAll(...(a as [])),
}));

function Probe() {
  const d = useDraft();
  return (
    <>
      <button type="button" onClick={() => d.setGraph((g) => addNode(g, { ...defaultNode(g, "wait"), id: `w${g.nodes.length + 1}` }))}>
        ajouter
      </button>
      <output aria-label="état">{d.saveState}</output>
    </>
  );
}

function show() {
  const ctx = { workspace: { slug: "team-a" }, can: () => true } as unknown as Ctx;
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}>
        <DraftProvider slug="flux">
          <Probe />
        </DraftProvider>
      </AppContext.Provider>
    </QueryClientProvider>,
  );
}

describe("brouillon : enregistrement automatique face à un serveur lent", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    Object.assign(server, { revision: 1, latency: 5000, inflight: 0, maxInflight: 0, puts: 0, conflicts: 0 });
    localStorage.clear();
    apiCall.mockReset();
    apiCall.mockImplementation(async (url: string, opts?: { method?: string; json?: { revision: number } }) => {
      if (opts?.method === "PUT" && url.endsWith("/draft/")) {
        server.puts += 1;
        server.inflight += 1;
        server.maxInflight = Math.max(server.maxInflight, server.inflight);
        const sent = opts.json?.revision;
        await new Promise((r) => setTimeout(r, server.latency));
        server.inflight -= 1;
        if (sent !== server.revision) {
          server.conflicts += 1;
          throw Object.assign(new Error("conflit"), { status: 409, details: { current_revision: server.revision } });
        }
        server.revision += 1;
        return { revision: server.revision, issues: [] };
      }
      return { slug: "flux", name: "Flux", draft_graph: emptyGraph(), draft_revision: server.revision, folder_level: null, latest_version: null };
    });
  });
  afterEach(() => vi.useRealTimers());

  it("n'envoie jamais deux écritures en même temps et ne se met pas en conflit avec lui-même", async () => {
    show();
    await screen.findByRole("button", { name: "ajouter" });
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole("button", { name: "ajouter" }));
    // l'écriture part après 1,2 s, la réponse arrive 5 s plus tard : pendant ce temps le client ne doit rien renvoyer
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1300);
    });
    expect(server.puts).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000);
    });
    expect(server.maxInflight, "une seule écriture à la fois").toBe(1);
    expect(server.conflicts, "aucun conflit avec soi-même").toBe(0);
    expect(screen.getByLabelText("état")).toHaveTextContent("saved");
  });

  it("une modification faite pendant l'écriture est enregistrée ensuite, avec la révision à jour", async () => {
    server.latency = 3000;
    show();
    const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await screen.findByRole("button", { name: "ajouter" });
    await u.click(screen.getByRole("button", { name: "ajouter" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1300);
    }); // 1re écriture en vol
    await u.click(screen.getByRole("button", { name: "ajouter" })); // 2e modification pendant l'écriture
    // L'écriture suivante est maintenant programmée à la fin de la précédente (et non pendant son vol) : on avance le temps par étapes,
    // car React ne rend qu'à la sortie de chaque `act`.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3500);
    }); // fin de la 1re écriture
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1300);
    }); // départ de la 2e
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    }); // fin de la 2e
    expect(server.conflicts).toBe(0);
    expect(server.maxInflight).toBe(1);
    expect(server.revision).toBe(3); // deux écritures acceptées, dans l'ordre
    expect(screen.getByLabelText("état")).toHaveTextContent("saved");
  });
});
