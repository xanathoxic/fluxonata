import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { FolderTree, inFolder } from "./FolderTree";

vi.mock("../api", async (orig) => ({ ...(await orig<typeof import("../api")>()), api: async () => ({ results: [], private: [] }) }));

describe("dossiers : seuls les éléments du dossier choisi s'affichent", () => {
  it("un dossier ne montre pas les éléments de ses sous-dossiers", () => {
    expect(inFolder("Infra", "Infra")).toBe(true);
    expect(inFolder("Infra/Réseau", "Infra")).toBe(false); // sous-dossier : non
    expect(inFolder("Infra/Réseau/Fabric", "Infra/Réseau")).toBe(false);
    expect(inFolder("Infra/Réseau", "Infra/Réseau")).toBe(true);
    expect(inFolder("Infrastructure", "Infra")).toBe(false); // même préfixe de texte, autre dossier
    expect(inFolder("", "Infra")).toBe(false);
  });

  it("« Tous » (aucun dossier choisi) montre tout, y compris ce qui est à la racine", () => {
    for (const folder of ["", "A", "A/B", "A/B/C"]) expect(inFolder(folder, "")).toBe(true);
  });

  it("l'arborescence annonce, pour chaque dossier, le nombre d'éléments de CE dossier (ce que la liste affichera), et « Tous » le total", async () => {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })) as unknown as typeof window.matchMedia; // absent de jsdom
    const ctx = { workspace: { slug: "team-a" }, can: () => false } as unknown as Ctx;
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AppContext.Provider value={ctx}>
          <FolderTree
            label="Dossiers de test"
            kind="workflow"
            canEdit={false}
            current=""
            onPick={() => undefined}
            folders={["Infra", "Infra", "Infra/Réseau", "Infra/Réseau/Fabric", "", "Autre"]}
          />
        </AppContext.Provider>
      </QueryClientProvider>,
    );
    const nav = await screen.findByRole("navigation", { name: "Dossiers de test" });
    expect(within(nav).getByRole("button", { name: /^Tous \(6\)/ })).toBeInTheDocument();
    expect(within(nav).getByRole("button", { name: /^Infra \(2\)/ })).toBeInTheDocument(); // 2 à elle, pas 4
    expect(within(nav).getByRole("button", { name: /^Autre \(1\)/ })).toBeInTheDocument();
  });
});
