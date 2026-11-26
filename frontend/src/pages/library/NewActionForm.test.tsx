import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../../context";
import { rolesChosen } from "./AnsibleFields";
import { NewActionForm, rolesPayload } from "./NewActionForm";

const api = vi.fn();
vi.mock("../../api", async (orig) => ({ ...(await orig<typeof import("../../api")>()), api: (...a: unknown[]) => api(...a) }));

function show() {
  const ctx = { workspace: { slug: "team-a" }, can: () => true } as unknown as Ctx;
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}>
        <MemoryRouter>
          <NewActionForm onDone={() => {}} />
        </MemoryRouter>
      </AppContext.Provider>
    </QueryClientProvider>,
  );
}
const posted = () => api.mock.calls.find((c) => (c[1] as { json?: unknown } | undefined)?.json !== undefined)?.[1].json as { version: Record<string, unknown> };

describe("Action : playbook Ansible", () => {
  beforeEach(() => {
    api.mockReset();
    api.mockImplementation(async (path: string, opts?: { json?: unknown }) => {
      if (opts?.json) return { slug: "deploy" };
      if (path.endsWith("/ansible-roles/"))
        return [
          { name: "web", description: "", latest_version: "1.2.0", versions: [{ version: "1.2.0" }, { version: "1.0.0" }] },
          { name: "common", description: "", latest_version: "1.0.0", versions: [{ version: "1.0.0" }] },
        ];
      if (path.endsWith("/actions/")) return { results: [] };
      return {};
    });
  });
  it("rolesPayload : « dernière » par défaut, version exacte si choisie", () => {
    expect(rolesPayload({ web: "", common: "1.0.0" })).toEqual([
      { role: "web", version: "latest" },
      { role: "common", version: "1.0.0" },
    ]);
    expect(rolesPayload({})).toEqual([]);
  });
  it("choisir Ansible remplace l'exemple Python par un playbook, ses schémas et le réseau sans accès", async () => {
    show();
    await userEvent.selectOptions(screen.getByLabelText("Langage"), "ansible");
    expect((screen.getByLabelText("Playbook (YAML)") as HTMLTextAreaElement).value).toContain("hosts: all");
    expect((screen.getByLabelText("Schéma des entrées (JSON Schema)") as HTMLTextAreaElement).value).toContain("inventory_ref");
    expect(screen.getByLabelText("Étiquettes de runner exigées (séparées par des virgules)")).toHaveValue("ansible");
    expect(screen.getByRole("option", { name: /Machines gérées/ })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Langage"), "python");
    expect((screen.getByLabelText("Code (point d'entrée : run)") as HTMLTextAreaElement).value).toContain("def run");
    expect(screen.queryByRole("option", { name: /Machines gérées/ })).toBeNull();
  });
  it("ne remplace pas le code déjà écrit en changeant de langage", async () => {
    show();
    const code = screen.getByLabelText("Code (point d'entrée : run)");
    await userEvent.clear(code);
    await userEvent.type(code, "echo mon code");
    await userEvent.selectOptions(screen.getByLabelText("Langage"), "ansible");
    expect(screen.getByLabelText("Playbook (YAML)")).toHaveValue("echo mon code");
  });
  it("publie avec les étiquettes, les rôles choisis (dernière ou exacte) et une mémoire suffisante", async () => {
    show();
    await userEvent.type(screen.getByLabelText(/Identifiant/), "deploy");
    await userEvent.type(screen.getByLabelText("Nom"), "Déployer");
    await userEvent.type(screen.getByLabelText(/Ce que fait l'action/), "Déploie la page.");
    await userEvent.selectOptions(screen.getByLabelText("Langage"), "ansible");
    const tags = screen.getByLabelText("Étiquettes de runner exigées (séparées par des virgules)");
    await userEvent.clear(tags);
    await userEvent.type(tags, "ansible, site-paris");
    const search = await screen.findByLabelText(/Ajouter un rôle/);
    expect(screen.queryByText("common")).toBeNull(); // rien n'est listé tant qu'on n'a pas cherché ou choisi
    await userEvent.type(search, "we");
    await userEvent.click(await screen.findByRole("button", { name: /Ajouter le rôle web/ }));
    expect(screen.queryByRole("button", { name: /Ajouter le rôle web/ })).toBeNull(); // déjà choisi : retiré des résultats
    await userEvent.type(screen.getByLabelText(/Ajouter un rôle/), "comm{Enter}"); // Entrée ajoute le premier résultat
    await userEvent.selectOptions(screen.getByLabelText("Version du rôle common"), "1.0.0");
    await userEvent.selectOptions(screen.getByLabelText("Politique réseau"), "targets");
    await userEvent.click(screen.getByRole("button", { name: "Retirer le rôle web" })); // retiré, puis ajouté de nouveau : l'ordre suit le dernier ajout
    await userEvent.type(screen.getByLabelText(/Ajouter un rôle/), "web{Enter}");
    await userEvent.click(screen.getByRole("button", { name: /Publier la version 1.0.0/ }));
    await waitFor(() => expect(posted()).toBeDefined());
    const v = posted().version;
    expect(v.runtime).toBe("ansible");
    expect(v.required_tags).toEqual(["ansible", "site-paris"]);
    expect(v.ansible_roles).toEqual([
      { role: "common", version: "1.0.0" },
      { role: "web", version: "latest" },
    ]);
    expect(v.network_policy).toBe("targets");
    expect(v.limits).toEqual({ timeout_seconds: 300, memory_mb: 256 });
  });
  it("une action Python n'envoie ni étiquettes ni rôles", async () => {
    show();
    await userEvent.type(screen.getByLabelText(/Identifiant/), "dbl");
    await userEvent.type(screen.getByLabelText("Nom"), "Doubler");
    await userEvent.click(screen.getByRole("button", { name: /Publier la version 1.0.0/ }));
    await waitFor(() => expect(posted()).toBeDefined());
    expect(posted().version).not.toHaveProperty("ansible_roles");
    expect(posted().version).not.toHaveProperty("required_tags");
  });
  it("rolesChosen : reprend les rôles déjà figés d'une version, et rien sans rôle", () => {
    expect(rolesChosen([{ role: "web", version: "1.2.0" }])).toEqual({ web: "1.2.0" });
    expect(rolesPayload(rolesChosen([{ role: "web", version: "1.2.0" }]))).toEqual([{ role: "web", version: "1.2.0" }]);
    expect(rolesChosen(undefined)).toEqual({});
  });
});
