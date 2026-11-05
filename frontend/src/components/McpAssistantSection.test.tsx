import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { McpAssistantSection, connectionSnippets } from "./McpAssistantSection";

const ws = { slug: "team-a", name: "Équipe A", role: "designer", permissions: ["view", "edit"], environments: [] };
const ctx = { me: { username: "dana", is_platform_admin: false, workspaces: [ws] }, workspace: ws, environment: "test", setWorkspace: () => undefined, setEnvironment: () => undefined, can: () => true } as Ctx;

function mount() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}><McpAssistantSection /></AppContext.Provider>
    </QueryClientProvider>,
  );
}
const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } }));

afterEach(() => { vi.restoreAllMocks(); });

describe("connectionSnippets", () => {
  it("builds the Claude Code command and a Claude Desktop config without the secret outside the env", () => {
    const s = connectionSnippets("https://x.test/api/v1/workspaces/a/mcp/design/", "orch_abc-_9");
    expect(s.code).toBe('claude mcp add --transport http --scope user fluxonata "https://x.test/api/v1/workspaces/a/mcp/design/" --header "Authorization: Bearer orch_abc-_9"');
    const d = JSON.parse(s.desktop);
    expect(d.mcpServers.fluxonata.env.FLUXONATA_AUTH).toBe("Bearer orch_abc-_9");
    expect(d.mcpServers.fluxonata.args.join(" ")).not.toContain("orch_abc");
    expect(d.mcpServers.fluxonata.args).not.toContain("--allow-http");
  });
  it("allows plain http only for an http address", () => {
    expect(JSON.parse(connectionSnippets("http://127.0.0.1:5173/x/", "t").desktop).mcpServers.fluxonata.args).toContain("--allow-http");
  });
});

describe("McpAssistantSection", () => {
  it("creates a token, shows the command once, and forgets it on close", async () => {
    const calls: { url: string; body?: string; method?: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input); calls.push({ url, body: init?.body as string, method: init?.method });
      if (init?.method === "POST") return json({ id: 1, name: "Mon assistant", prefix: "orch_abcdef", scopes: ["view", "edit"], created_at: "2026-11-05T10:00:00Z", expires_at: "2026-11-05T18:00:00Z", last_used_at: null, token: "orch_SECRET-value", notice: "" }, 201);
      return json({ results: [], allowed_scopes: ["view", "edit"] });
    });
    mount();
    await screen.findByText(/Aucun jeton actif/);
    await userEvent.click(screen.getByRole("button", { name: "Générer un jeton" }));
    await userEvent.click(screen.getByRole("button", { name: "Générer le jeton" }));
    const post = calls.find((c) => c.method === "POST");
    expect(JSON.parse(post?.body ?? "{}")).toEqual({ name: "Mon assistant", hours: 8, write: true });
    expect(await screen.findByLabelText("Jeton")).toHaveTextContent(/^orch_SECRET-value$/);
    expect(await screen.findByLabelText("Commande Claude Code")).toHaveTextContent("Bearer orch_SECRET-value");
    expect(screen.getByText(/ne sera plus jamais affiché/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Claude Desktop" }));
    expect(screen.getByLabelText("Configuration Claude Desktop")).toHaveTextContent("mcp-remote");
    await userEvent.click(screen.getByRole("tab", { name: "Codex" }));
    expect(screen.getByLabelText("Variable Codex")).toHaveTextContent('export FLUXONATA_TOKEN="orch_SECRET-value"');
    expect(screen.getByLabelText("Configuration Codex")).toHaveTextContent('bearer_token_env_var = "FLUXONATA_TOKEN"');
    expect(screen.getByLabelText("Configuration Codex")).not.toHaveTextContent("orch_SECRET");
    await userEvent.click(screen.getByRole("button", { name: /fermer/ }));
    await waitFor(() => expect(document.body).not.toHaveTextContent("orch_SECRET-value"));
  });

  it("offers read-only only when the role cannot edit", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => json({ results: [], allowed_scopes: ["view"] }));
    mount();
    await screen.findByText(/Aucun jeton actif/);
    await userEvent.click(screen.getByRole("button", { name: "Générer un jeton" }));
    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(screen.getByText(/lecture seule/)).toBeInTheDocument();
  });

  it("revokes after confirmation", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      calls.push(`${init?.method ?? "GET"} ${String(input)}`);
      if (init?.method === "DELETE") return Promise.resolve(new Response(null, { status: 204 }));
      return json({ results: [{ id: 7, name: "Ancien", prefix: "orch_zz", scopes: ["view"], created_at: "2026-11-05T10:00:00Z", expires_at: "2026-11-05T18:00:00Z", last_used_at: null }], allowed_scopes: ["view"] });
    });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Révoquer" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Révoquer" }).at(-1)!);
    await waitFor(() => expect(calls.some((c) => c.startsWith("DELETE") && c.endsWith("/me/mcp-tokens/7/"))).toBe(true));
  });
});

describe("when the server cannot be reached", () => {
  it("never shows a bare « Erreur » and does not claim the role is read-only", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((_i, init) => init?.method === "POST" ? json({}, 500) : json({}, 500));
    mount();
    await screen.findByText(/Impossible de charger vos jetons/);
    await userEvent.click(screen.getByRole("button", { name: "Générer un jeton" }));
    expect(screen.getByRole("checkbox")).toBeEnabled();
    expect(screen.queryByText(/Votre rôle ne permet pas/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Générer le jeton" }));
    const alert = await screen.findByText(/Le serveur n'a pas pu traiter/);
    expect(alert).not.toHaveTextContent(/^Erreur : Erreur/);
  });
});
