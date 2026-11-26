import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../../context";
import { RolePage } from "./RolePage";
import { RolesPage } from "./RolesPage";
import type { RoleSummary } from "./roles";

const api = vi.fn();
vi.mock("../../api", async (orig) => ({ ...(await orig<typeof import("../../api")>()), api: (...a: unknown[]) => api(...a) }));

// l'éditeur de code (CodeMirror) ne se pilote pas sous jsdom : un champ texte portant le même nom et le même état de lecture seule le remplace
vi.mock("../../components/LazyCodeEditor", () => ({
  LazyCodeEditor: ({ value, onChange, ariaLabel, readOnly }: { value: string; onChange: (v: string) => void; ariaLabel: string; readOnly?: boolean }) => (
    <textarea aria-label={ariaLabel} value={value} readOnly={readOnly} onChange={(e) => onChange(e.target.value)} />
  ),
}));

// absent de jsdom : l'arbre de dossiers s'adapte à la largeur de l'écran
window.matchMedia = ((query: string) => ({
  matches: false,
  media: query,
  addEventListener: () => undefined,
  removeEventListener: () => undefined,
})) as unknown as typeof window.matchMedia;

const FILES1 = { "tasks/main.yml": "- name: a\n  ansible.builtin.debug: {msg: x}\n", "defaults/main.yml": "message: bonjour\n" };
const ROLE: RoleSummary = {
  name: "web",
  summary: "",
  contract_ok: false,
  variables: 0,
  description: "Serveur web",
  folder: "",
  latest_version: "1.1.0",
  used_by: 2,
  versions: [
    { version: "1.1.0", digest: "sha256:bbb", changelog: "deuxième", files: 2, created_at: "2026-11-25T10:00:00Z" },
    { version: "1.0.0", digest: "sha256:aaa", changelog: "première", files: 2, created_at: "2026-11-24T10:00:00Z" },
  ],
};
const posts = () => api.mock.calls.filter((c) => (c[1] as { json?: unknown } | undefined)?.json !== undefined);

function serve() {
  api.mockImplementation(async (path: string, opts?: { method?: string; json?: { files?: Record<string, string>; version?: string } }) => {
    if (opts?.method === "DELETE") return {};
    if (path.endsWith("/versions/") && opts?.json) return { ...ROLE, latest_version: opts.json.version };
    const m = path.match(/\/versions\/([^/]+)\/$/);
    if (m)
      return {
        version: m[1],
        digest: `sha256:${m[1] === "1.0.0" ? "a" : "b"}`.padEnd(31, m[1] === "1.0.0" ? "a" : "b"),
        changelog: m[1] === "1.0.0" ? "première" : "deuxième",
        files: m[1] === "1.0.0" ? FILES1 : { ...FILES1, "defaults/main.yml": "message: salut\n" },
      };
    if (path.endsWith("/ansible-roles/") && opts?.json) return { ...ROLE, name: (opts.json as unknown as { name: string }).name };
    if (path.endsWith("/ansible-roles/")) return [ROLE];
    if (/\/ansible-roles\/[^/]+\/$/.test(path)) return ROLE; // aussi le rôle qu'on vient de créer : la page y navigue
    return {};
  });
}
function show(ui: "list" | "page", can = true) {
  const ctx = { workspace: { slug: "team-a" }, can: () => can } as unknown as Ctx;
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}>
        <MemoryRouter initialEntries={[ui === "list" ? "/library/ansible-roles" : "/library/ansible-roles/web"]}>
          <Routes>
            <Route path="/library/ansible-roles" element={<RolesPage />} />
            <Route path="/library/ansible-roles/:name" element={<RolePage />} />
          </Routes>
        </MemoryRouter>
      </AppContext.Provider>
    </QueryClientProvider>,
  );
}

describe("Rôles Ansible : liste", () => {
  beforeEach(() => {
    api.mockReset();
    serve();
  });
  it("liste les rôles avec leur dernière version et leurs usages", async () => {
    show("list");
    expect(await screen.findByRole("link", { name: "web" })).toHaveAttribute("href", "/library/ansible-roles/web");
    expect(screen.getByText("1.1.0")).toBeInTheDocument();
    expect(screen.getByText("2 versions d'action")).toBeInTheDocument();
  });
  it("crée un rôle : nom contrôlé, doublon refusé, fichiers de départ envoyés", async () => {
    show("list");
    await userEvent.click(await screen.findByRole("button", { name: /Nouveau rôle/ }));
    const dialog = await screen.findByRole("dialog");
    const name = within(dialog).getByLabelText("Nom du rôle");
    await userEvent.type(name, "Web-1");
    expect(within(dialog).getByText("Ce nom n'est pas valide.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Créer" })).toBeDisabled();
    await userEvent.clear(name);
    await userEvent.type(name, "web");
    expect(within(dialog).getByText("Ce nom existe déjà.")).toBeInTheDocument();
    await userEvent.clear(name);
    await userEvent.type(name, "base_systeme");
    await userEvent.click(within(dialog).getByRole("button", { name: "Créer" }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    const sent = posts()[0]?.[1].json as { name: string; files: Record<string, string> };
    expect(sent.name).toBe("base_systeme");
    expect(Object.keys(sent.files).sort()).toEqual(["defaults/main.yml", "tasks/main.yml"]);
  });
  it("un lecteur ne voit ni création ni suppression", async () => {
    show("list", false);
    await screen.findByRole("link", { name: "web" });
    expect(screen.queryByRole("button", { name: /Nouveau rôle/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Supprimer/ })).toBeNull();
  });
});

describe("Rôle Ansible : édition", () => {
  beforeEach(() => {
    api.mockReset();
    serve();
  });
  it("affiche la dernière version, ses fichiers par dossier, l'empreinte et le nom à appeler", async () => {
    show("page");
    expect(await screen.findByRole("heading", { name: "web" })).toBeInTheDocument();
    expect(screen.getByText("roles: [web]")).toBeInTheDocument();
    expect(await screen.findByLabelText("Contenu de tasks/main.yml")).toBeInTheDocument();
    const nav = within(screen.getByRole("navigation", { name: "Fichiers du rôle" }));
    expect(await nav.findByRole("button", { name: "tasks/main.yml", current: true })).toBeInTheDocument();
    expect(screen.getByText(/sha256:bbbbbbbbbbbb/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publier une nouvelle version…" })).toBeDisabled(); // rien n'a changé
  });
  it("modifie un fichier puis publie une nouvelle version avec le numéro proposé", async () => {
    show("page");
    const nav = within(await screen.findByRole("navigation", { name: "Fichiers du rôle" }));
    await userEvent.click(await nav.findByRole("button", { name: "defaults/main.yml" }));
    const text = await screen.findByLabelText("Contenu de defaults/main.yml");
    await userEvent.clear(text);
    await userEvent.type(text, "message: nouveau");
    expect(await screen.findByText(/Modifications non publiées/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Publier une nouvelle version…" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Numéro de version")).toHaveValue("1.1.1");
    await userEvent.type(within(dialog).getByLabelText("Ce qui change (facultatif)"), "nouveau message");
    await userEvent.click(within(dialog).getByRole("button", { name: "Publier" }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    const sent = posts()[0]?.[1].json as { version: string; changelog: string; files: Record<string, string> };
    expect(sent.version).toBe("1.1.1");
    expect(sent.changelog).toBe("nouveau message");
    expect(sent.files["defaults/main.yml"]).toBe("message: nouveau");
    expect(sent.files["tasks/main.yml"]).toContain("ansible.builtin.debug");
  });
  it("barre d'outils de l'explorateur : tout réduire, nouveau dossier, actualiser en abandonnant les modifications", async () => {
    show("page");
    const nav = within(await screen.findByRole("navigation", { name: "Fichiers du rôle" }));
    expect(await nav.findByRole("button", { name: "tasks/main.yml" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Réduire tous les dossiers" }));
    expect(nav.queryByRole("button", { name: "tasks/main.yml" })).toBeNull(); // dossiers repliés : plus de fichiers listés
    expect(nav.getAllByRole("button", { expanded: false }).length).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole("button", { name: "Ajouter un dossier" })); // propose un dossier standard pas encore créé
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Chemin du fichier")).toHaveValue("handlers/main.yml");
    await userEvent.click(within(dialog).getByRole("button", { name: "Annuler" }));

    await userEvent.click(nav.getByRole("button", { name: /tasks\// })); // rouvre le dossier
    const text = await screen.findByLabelText("Contenu de tasks/main.yml");
    await userEvent.type(text, " modifié");
    expect(await screen.findByText(/Modifications non publiées/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Actualiser les fichiers" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Abandonner" }));
    await waitFor(() => expect(screen.queryByText(/Modifications non publiées/)).toBeNull());
    expect(screen.getByLabelText("Contenu de tasks/main.yml")).not.toHaveValue(expect.stringContaining("modifié"));
  });
  it("onglet Contrat : le compléter rend la version modifiée, et la publication l'envoie avec les fichiers", async () => {
    show("page");
    await screen.findByLabelText("Contenu de tasks/main.yml");
    await userEvent.click(screen.getByRole("tab", { name: /Contrat/ }));
    expect(await screen.findByText(/point\(s\) à compléter/)).toBeInTheDocument(); // aucune phrase d'usage encore
    await userEvent.type(screen.getByLabelText(/À quoi sert le rôle/), "Installe le serveur web.");
    await userEvent.click(screen.getByRole("button", { name: /Ajouter une variable/ }));
    await userEvent.type(screen.getByLabelText("Description de variable_1"), "Un réglage.");
    await userEvent.type(screen.getByLabelText("Valeur par défaut de variable_1"), "abc");
    await userEvent.tab();
    expect(await screen.findByText(/Modifications non publiées/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Générer depuis les variables" }));
    expect((screen.getByLabelText("Un playbook qui utilise le rôle") as HTMLTextAreaElement).value).toContain('variable_1: "abc"');
    await userEvent.click(screen.getByRole("button", { name: "Publier une nouvelle version…" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Publier" }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    const sent = posts()[0]?.[1].json as { contract: { summary: string; variables: { name: string; description: string; default: unknown }[] } };
    expect(sent.contract.summary).toBe("Installe le serveur web.");
    expect(sent.contract.variables).toEqual([expect.objectContaining({ name: "variable_1", description: "Un réglage.", default: "abc" })]);
  });
  it("le contrat d'une version publiée se lit comme une documentation, sans champ à modifier pour un lecteur", async () => {
    show("page", false);
    await screen.findByLabelText("Contenu de tasks/main.yml");
    await userEvent.click(screen.getByRole("tab", { name: /Contrat/ }));
    expect(await screen.findByText(/n'a pas de contrat/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/À quoi sert le rôle/)).toBeNull();
  });
  it("refuse un numéro de version invalide", async () => {
    show("page");
    const text = await screen.findByLabelText("Contenu de tasks/main.yml");
    await userEvent.type(text, " # plus");
    await userEvent.click(screen.getByRole("button", { name: "Publier une nouvelle version…" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.clear(within(dialog).getByLabelText("Numéro de version"));
    await userEvent.type(within(dialog).getByLabelText("Numéro de version"), "1.2");
    expect(within(dialog).getByText(/1.2.0 est attendu/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Publier" })).toBeDisabled();
  });
  it("ajoute un fichier avec un chemin contrôlé et son contenu de départ", async () => {
    show("page");
    await userEvent.click(await screen.findByRole("button", { name: /Ajouter un fichier/ }));
    const dialog = await screen.findByRole("dialog");
    const path = within(dialog).getByLabelText("Chemin du fichier");
    await userEvent.clear(path);
    await userEvent.type(path, "library/x.py");
    expect(within(dialog).getByText(/n'existe pas dans un rôle/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Ajouter" })).toBeDisabled();
    await userEvent.clear(path);
    await userEvent.type(path, "handlers/main.yml");
    await userEvent.click(within(dialog).getByRole("button", { name: "Ajouter" }));
    expect(((await screen.findByLabelText("Contenu de handlers/main.yml")) as HTMLTextAreaElement).value).toContain("mon_handler");
  });
  it("empêche de supprimer tasks/main.yml pour publier", async () => {
    show("page");
    await userEvent.click(await screen.findByRole("button", { name: "Supprimer le fichier tasks/main.yml" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/tasks\/main.yml/);
    expect(screen.getByRole("button", { name: "Publier une nouvelle version…" })).toBeDisabled();
  });
  it("une version ancienne est en lecture seule", async () => {
    show("page");
    await userEvent.selectOptions(await screen.findByLabelText("Version"), "1.0.0");
    expect(await screen.findByText(/Version ancienne : lecture seule/)).toBeInTheDocument();
    expect(await screen.findByLabelText("Contenu de tasks/main.yml")).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: /Ajouter un fichier/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Publier une nouvelle version…" })).toBeDisabled();
  });
  it("un lecteur consulte sans pouvoir modifier", async () => {
    show("page", false);
    expect(await screen.findByLabelText("Contenu de tasks/main.yml")).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: "Publier une nouvelle version…" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Supprimer le fichier/ })).toBeNull();
  });
});
