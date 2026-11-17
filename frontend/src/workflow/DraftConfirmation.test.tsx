import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { DraftProvider, useDraft } from "./DraftContext";
import { emptyGraph, updateNode } from "./graphOps";
import type { Graph } from "./types";

/** « Brouillon enregistré » ne doit jamais s'afficher avant que le serveur ait confirmé la valeur qu'il a réellement stockée. */
const DRAFT: Graph = { ...emptyGraph(), nodes: [{ id: "c", type: "subworkflow", name: "Appel", workflow: "child", version: "1.1.0" }] } as Graph;
type Reply = (sent: { graph: Graph; revision: number }) => Promise<unknown>;
const server: { reply: Reply; puts: { graph: Graph; revision: number }[] } = { reply: async () => ({}), puts: [] };
const apiCall = vi.fn();
vi.mock("../api", async (orig) => ({
  ...(await orig<typeof import("../api")>()),
  api: (...a: unknown[]) => apiCall(...a),
  apiAll: async () => ({ results: [], count: 0, next: null }),
}));

let lastSave: boolean | null = null;
function Probe() {
  const d = useDraft();
  return (
    <>
      <button type="button" onClick={() => d.setGraph((g) => updateNode(g, "c", { version: "1.2.0" }))}>
        choisir 1.2.0
      </button>
      <button
        type="button"
        onClick={() =>
          void d.save().then((ok) => {
            lastSave = ok;
          })
        }
      >
        enregistrer
      </button>
      <output aria-label="état">{d.saveState}</output>
      <output aria-label="message">{d.saveMessage}</output>
      <output aria-label="version affichée">{d.graph.nodes[0]?.version}</output>
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
const state = () => screen.getByLabelText("état").textContent;
const later = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

describe("brouillon : la version choisie n'est « enregistrée » que lorsque le serveur la confirme", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    localStorage.clear();
    lastSave = null;
    server.puts = [];
    apiCall.mockReset();
    apiCall.mockImplementation(async (url: string, opts?: { method?: string; json?: { graph: Graph; revision: number } }) => {
      if (opts?.method === "PUT" && url.endsWith("/draft/") && opts.json) {
        server.puts.push(opts.json);
        return server.reply(opts.json);
      }
      return { slug: "flux", name: "Flux", draft_graph: DRAFT, draft_revision: 96, folder_level: null, latest_version: null };
    });
  });
  afterEach(() => vi.useRealTimers());

  it("envoie 1.2.0 avec la révision courante, reste « en cours » pendant l'écriture, puis confirme", async () => {
    server.reply = async (sent) => {
      await new Promise((r) => setTimeout(r, 3000));
      return { revision: sent.revision + 1, issues: [], graph: sent.graph };
    };
    show();
    const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await screen.findByRole("button", { name: "choisir 1.2.0" });
    expect(screen.getByLabelText("version affichée")).toHaveTextContent("1.1.0"); // l'ouverture n'a rien changé
    await u.click(screen.getByRole("button", { name: "choisir 1.2.0" }));
    expect(state()).toBe("dirty");
    await later(1300);
    expect(state()).toBe("saving"); // jamais « saved » avant la réponse
    expect(server.puts).toHaveLength(1);
    expect(server.puts[0]?.revision).toBe(96);
    expect(server.puts[0]?.graph.nodes[0]?.version).toBe("1.2.0");
    await later(3500);
    expect(state()).toBe("saved");
    expect(screen.getByLabelText("version affichée")).toHaveTextContent("1.2.0");
  });

  it("si le serveur répond avec une autre valeur, l'éditeur ne dit PAS « enregistré » et l'explique", async () => {
    server.reply = async (sent) => ({ revision: sent.revision + 1, issues: [], graph: DRAFT }); // le serveur a conservé 1.1.0
    show();
    const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await screen.findByRole("button", { name: "choisir 1.2.0" });
    await u.click(screen.getByRole("button", { name: "choisir 1.2.0" }));
    await later(1500);
    expect(state()).toBe("error");
    expect(screen.getByLabelText("message")).toHaveTextContent("contenu différent");
    await later(20000);
    expect(server.puts, "pas de boucle d'écritures").toHaveLength(1);
  });

  it("la même valeur avec les clés dans un autre ordre (jsonb) est bien une confirmation", async () => {
    server.reply = async (sent) => {
      const n = sent.graph.nodes[0] as unknown as Record<string, unknown>;
      const reordered = Object.fromEntries(Object.entries(n).reverse());
      return { revision: sent.revision + 1, issues: [], graph: { ...sent.graph, nodes: [reordered] } };
    };
    show();
    const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await screen.findByRole("button", { name: "choisir 1.2.0" });
    await u.click(screen.getByRole("button", { name: "choisir 1.2.0" }));
    await later(1500);
    expect(state()).toBe("saved");
  });

  it("un refus du serveur (400) est annoncé avec son motif, n'est pas renvoyé en boucle, et save() rend faux", async () => {
    server.reply = async () => {
      throw Object.assign(new Error("Graphe invalide : étape c"), { status: 400 });
    };
    show();
    const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await screen.findByRole("button", { name: "choisir 1.2.0" });
    await u.click(screen.getByRole("button", { name: "choisir 1.2.0" }));
    await later(1500);
    expect(state()).toBe("invalid");
    expect(screen.getByLabelText("message")).toHaveTextContent("refusé par le serveur : Graphe invalide : étape c");
    await later(20000);
    expect(server.puts, "le même contenu refusé n'est pas renvoyé").toHaveLength(1);
    await u.click(screen.getByRole("button", { name: "enregistrer" }));
    await later(1500);
    expect(lastSave).toBe(false);
    expect(state()).toBe("invalid");
  });

  it("un conflit de révision (409) est signalé clairement, rien n'est écrasé", async () => {
    server.reply = async () => {
      throw Object.assign(new Error("conflit"), { status: 409, details: { current_revision: 97 } });
    };
    show();
    const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await screen.findByRole("button", { name: "choisir 1.2.0" });
    await u.click(screen.getByRole("button", { name: "choisir 1.2.0" }));
    await later(1500);
    expect(state()).toBe("conflict");
    expect(screen.getByLabelText("message")).toHaveTextContent("modifié ailleurs (révision 97 au lieu de 96)");
    await later(20000);
    expect(server.puts, "aucune réécriture automatique").toHaveLength(1);
  });

  it("save() ne rend vrai qu'une fois la valeur confirmée (garde-fou de la publication)", async () => {
    server.reply = async (sent) => ({ revision: sent.revision + 1, issues: [], graph: sent.graph });
    show();
    const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await screen.findByRole("button", { name: "choisir 1.2.0" });
    await u.click(screen.getByRole("button", { name: "choisir 1.2.0" }));
    await u.click(screen.getByRole("button", { name: "enregistrer" }));
    await later(500);
    expect(lastSave).toBe(true);
    expect(state()).toBe("saved");
  });
});
