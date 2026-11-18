import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { DefinitionEditor } from "./DefinitionEditor";
import { emptyDefinition } from "./types";

const api = vi.fn();
vi.mock("../api", async (orig) => ({ ...(await orig<typeof import("../api")>()), api: (...a: unknown[]) => api(...a) }));

beforeAll(() => {
  // jsdom n'a pas matchMedia
  window.matchMedia = ((q: string) => ({
    matches: false,
    media: q,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    onchange: null,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
});

const SPEC = { version: 1, mode: "group", parts: [] };
const card = (slug: string, name: string) => ({
  slug,
  name,
  description: "",
  icon: "custom",
  published: { version: "1.0.0", name, icon: "custom", spec: SPEC },
});

async function show(selfSlug?: string) {
  api.mockImplementation(async () => ({ results: [card("test", "Test simple"), card("test23", "Test lignes")] }));
  const ctx = { workspace: { slug: "team-a" }, can: () => true } as unknown as Ctx;
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}>
        <MemoryRouter>
          <DefinitionEditor control selfSlug={selfSlug} def={emptyDefinition()} onChange={() => undefined} edit schema={undefined} connections={[]} />
        </MemoryRouter>
      </AppContext.Provider>
    </QueryClientProvider>,
  );
  await screen.findByRole("button", { name: /Test simple/ });
}

describe("Palette des sous-champs d'un contrôle", () => {
  it("n'offre pas le contrôle en cours de conception, mais offre les autres", async () => {
    await show("test23");
    expect(screen.getByRole("button", { name: /Test simple/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Test lignes/ })).toBeNull();
  });
  it("offre tous les contrôles publiés quand aucun n'est en cours de conception (formulaire)", async () => {
    await show(undefined);
    expect(screen.getByRole("button", { name: /Test lignes/ })).toBeTruthy();
  });
});
