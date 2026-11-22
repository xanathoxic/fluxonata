import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SsoTab } from "./SsoTab";
import type { Listing, Provider } from "./ssoTypes";

const api = vi.fn();
vi.mock("../../api", async (orig) => ({ ...(await orig<typeof import("../../api")>()), api: (...a: unknown[]) => api(...a) }));

const ROLES = [
  { id: "designer", label: "Concepteur" },
  { id: "viewer", label: "Lecteur" },
];
const LISTING = (results: Provider[], environment: Listing["environment"] = null): Listing => ({
  results,
  environment,
  base_url: "http://x",
  public_url_configured: true,
  roles: ROLES,
  name_id_formats: [{ id: "", label: "Selon le fournisseur" }],
});
const OIDC: Provider = {
  slug: "corp",
  name: "Annuaire",
  kind: "oidc",
  enabled: true,
  auto_create: true,
  unmapped: "allow",
  config: { issuer: "https://idp.example.org", client_id: "f", scopes: "openid profile email", token_auth: "basic", pkce: true },
  has_secret: true,
  endpoints: { login: "http://x/l", callback: "http://x/c" },
  rules: 2,
  identities: 5,
  updated_at: "",
};

function show(listing: Listing) {
  api.mockImplementation(async (path: string, opts?: { method?: string; json?: unknown }) => {
    if (path === "/platform/workspaces/") return { results: [{ slug: "team-a", name: "Équipe A", archived: false }] };
    if (path.endsWith("/rules/") && !opts?.method)
      return { results: [{ group: "orch-d", match: "exact", grant: "workspace", workspace: "team-a", role: "designer", user_group: "" }] };
    if (path.endsWith("/simulate/"))
      return {
        groups: ["orch-d"],
        matched: [{ rule: 1, group: "orch-d", pattern: "orch-d" }],
        workspaces: [{ workspace: "team-a", name: "Équipe A", role: "designer", user_groups: [] }],
        platform_admin: false,
        manages_platform_admin: false,
        login_allowed: true,
        login_denied_reason: "",
      };
    if (opts?.method === "PATCH") return { ...OIDC, ...(opts.json as object) };
    if (path === "/platform/sso/providers/") return listing;
    return {};
  });
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SsoTab />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  api.mockReset();
});

describe("administration du SSO", () => {
  it("sans fournisseur, propose OpenID Connect et SAML", async () => {
    show(LISTING([]));
    expect(await screen.findByText("Aucun fournisseur d'identité")).toBeTruthy();
    expect(screen.getByRole("button", { name: /OpenID Connect/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /SAML 2.0/ })).toBeTruthy();
  });

  it("liste les fournisseurs avec leur état, et montre celui de l'environnement sans le rendre modifiable", async () => {
    show(LISTING([OIDC], { issuer: "https://legacy.example", client_id: "legacy", login: "/x", group_roles: true }));
    const card = await screen.findByRole("region", { name: "Annuaire" });
    expect(within(card).getAllByText("Actif").length).toBeGreaterThan(0);
    expect(within(card).getByText("OpenID Connect")).toBeTruthy();
    expect(card.textContent).toContain("2");
    const env = screen.getByRole("region", { name: "Fournisseur défini par l'environnement" });
    expect(env.textContent).toContain("https://legacy.example");
    expect(within(env).queryByRole("button")).toBeNull();
  });

  it("n'envoie jamais le secret enregistré et ne l'affiche pas à la modification", async () => {
    const u = userEvent.setup();
    show(LISTING([OIDC]));
    await u.click(await screen.findByRole("button", { name: /^Configurer/ }));
    const secret = screen.getByLabelText("Secret du client") as HTMLInputElement;
    expect(secret.value).toBe("");
    expect(secret.placeholder).toContain("enregistré");
    await u.click(screen.getByRole("button", { name: "Enregistrer" }));
    const patch = api.mock.calls.find((c) => (c[1] as { method?: string } | undefined)?.method === "PATCH");
    expect(JSON.stringify(patch?.[1])).not.toContain("secret");
  });

  it("édite les règles groupe → rôle, les envoie en bloc et simule", async () => {
    const u = userEvent.setup();
    show(LISTING([OIDC]));
    await u.click(await screen.findByRole("button", { name: /^Configurer/ }));
    await u.click(screen.getByRole("tab", { name: /Comptes et droits/ }));
    expect(await screen.findByDisplayValue("orch-d")).toBeTruthy();
    await u.click(screen.getByRole("button", { name: "Ajouter une règle" }));
    await u.type(screen.getAllByLabelText("Groupe du fournisseur")[1] as HTMLElement, "orch-v");
    await u.selectOptions(screen.getAllByLabelText("Espace")[1] as HTMLElement, "team-a");
    await u.selectOptions(screen.getAllByLabelText("Rôle")[1] as HTMLElement, "viewer");
    await u.click(screen.getByRole("button", { name: "Enregistrer les droits" }));
    const put = api.mock.calls.find((c) => (c[1] as { method?: string } | undefined)?.method === "PUT");
    if (!put) throw new Error("aucun enregistrement des règles");
    const rules = (put[1] as { json: { rules: { group: string; workspace: string; role: string }[] } }).json.rules;
    expect(rules.map((r) => [r.group, r.workspace, r.role])).toEqual([
      ["orch-d", "team-a", "designer"],
      ["orch-v", "team-a", "viewer"],
    ]);
    await u.type(screen.getByLabelText(/Groupes d'une personne/), "orch-d");
    await u.click(screen.getByRole("button", { name: /Simuler avec/ }));
    expect(await screen.findByText("Connexion autorisée")).toBeTruthy();
    expect(screen.getByText(/Espace/, { selector: "li" }).textContent).toContain("Concepteur");
  });
});
