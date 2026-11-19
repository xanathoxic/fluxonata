import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { ExtensionPage } from "./ExtensionPage";
import { extensionPath, type ExtensionHost, type ExtensionInfo } from "./types";
import { useExtensions } from "./useExtensions";

const api = vi.fn();
vi.mock("../api", async (orig) => ({ ...(await orig<typeof import("../api")>()), api: (...a: unknown[]) => api(...a) }));

const INFO: ExtensionInfo = {
  id: "exemple",
  name: "Exemple",
  version: "1.0.0",
  description: "",
  ui: "/api/v1/workspaces/team-a/extensions/exemple/ui/index.js",
  menu: [],
};
const CTX = { workspace: { slug: "team-a", name: "Équipe A" }, can: () => true } as unknown as Ctx;

function show(extensions: ExtensionInfo[], load: (url: string) => Promise<{ default?: unknown }>, at = "/x/exemple/reglages") {
  return render(
    <AppContext.Provider value={CTX}>
      <MemoryRouter initialEntries={[at]}>
        <Routes>
          <Route path="/x/:ext/*" element={<ExtensionPage extensions={extensions} me={{ username: "alice" }} load={load} />} />
        </Routes>
      </MemoryRouter>
    </AppContext.Provider>,
  );
}

describe("page d'extension", () => {
  it("monte le module avec le seul objet host, puis le démonte en quittant la page", async () => {
    const stop = vi.fn();
    let received: ExtensionHost | undefined;
    const load = vi.fn(async () => ({
      default: (el: HTMLElement, host: ExtensionHost) => {
        received = host;
        el.textContent = "contenu de l'extension";
        return stop;
      },
    }));
    const { unmount } = show([INFO], load);
    expect(await screen.findByText("contenu de l'extension")).toBeInTheDocument();
    expect(load).toHaveBeenCalledWith(INFO.ui);
    expect(received).toMatchObject({ apiVersion: 1, workspace: { slug: "team-a" }, user: { username: "alice" }, path: "/reglages" });
    expect(Object.keys(received ?? {}).sort()).toEqual(["api", "apiVersion", "navigate", "path", "user", "workspace"]);
    unmount();
    expect(stop).toHaveBeenCalledOnce();
  });

  it("le host appelle l'API de la plateforme", async () => {
    api.mockResolvedValue({ ok: true });
    let host: ExtensionHost | undefined;
    show([INFO], async () => ({
      default: (_el: HTMLElement, h: ExtensionHost) => {
        host = h;
        return undefined;
      },
    }));
    await waitFor(() => expect(host).toBeDefined());
    await expect(host?.api("/workspaces/team-a/plugins/exemple/ping/")).resolves.toEqual({ ok: true });
    expect(api).toHaveBeenCalledWith("/workspaces/team-a/plugins/exemple/ping/", undefined);
  });

  it("un module qui ne se charge pas, ou sans fonction par défaut, donne un message sans casser la page", async () => {
    show([INFO], async () => {
      throw new Error("réseau");
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("n'a pas pu être chargée");
    show([INFO], async () => ({ default: 42 }));
    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
  });

  it("une extension inconnue ou sans page est signalée", () => {
    show([], async () => ({}));
    expect(screen.getByRole("alert")).toHaveTextContent("introuvable");
  });

  it("une erreur levée à l'intérieur du module est contenue", async () => {
    show([INFO], async () => ({
      default: () => {
        throw new Error("bug d'extension");
      },
    }));
    expect(await screen.findByRole("alert")).toHaveTextContent("n'a pas pu être chargée");
  });
});

describe("liste des extensions", () => {
  const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;

  it("renvoie celles du serveur, et une liste vide en cas de panne ou si désactivé", async () => {
    api.mockResolvedValueOnce({ api: 1, extensions: [INFO] });
    const ok = renderHook(() => useExtensions("team-a", true), { wrapper });
    await waitFor(() => expect(ok.result.current).toEqual([INFO]));
    api.mockRejectedValueOnce(new Error("503"));
    const down = renderHook(() => useExtensions("team-b", true), { wrapper });
    await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
    expect(down.result.current).toEqual([]);
    api.mockClear();
    expect(renderHook(() => useExtensions("team-c", false), { wrapper }).result.current).toEqual([]);
    expect(api).not.toHaveBeenCalled();
  });

  it("l'adresse d'une entrée de menu est sous /x/<extension>", () => {
    expect(extensionPath("exemple", "/")).toBe("/x/exemple");
    expect(extensionPath("exemple", "/reglages")).toBe("/x/exemple/reglages");
  });
});
