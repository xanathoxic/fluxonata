import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render as plainRender, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { ChoiceSourceOverlay } from "./ServiceOverlay";
import type { FormField } from "./types";

const ws = { slug: "team-a", name: "Équipe A", role: "designer", permissions: ["view", "edit"], environments: [] };
const ctx = { me: { username: "dana", is_platform_admin: false, workspaces: [ws] }, workspace: ws, environment: "test", setWorkspace: () => undefined, setEnvironment: () => undefined, can: () => true } as Ctx;
/** L'essai (« Lire cette réponse ») a besoin de l'espace courant et de React Query, comme dans l'application. */
const render = (ui: ReactElement) => plainRender(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AppContext.Provider value={ctx}>{ui}</AppContext.Provider></QueryClientProvider>,
);

const field: FormField = { key: "groupe", type: "select", label: "Groupe", source: { connection: "svc", path: "/politique", items_path: "groups" } };

const tab = (name: RegExp) => screen.getByRole("tab", { name });
/** Un champ est affiché si son panneau d'onglet n'est pas masqué par un `display: none` EN LIGNE. L'attribut `hidden` seul ne suffit pas dans
 *  l'application (`.sv-pane { display: grid }` l'emporte), et jsdom ne reproduit pas ce conflit de cascade : c'est donc le style posé par le
 *  composant que l'on vérifie. */
const visible = (label: string) => screen.getAllByLabelText(label).some((el) => el.closest<HTMLElement>('[role="tabpanel"]')?.style.display !== "none");

describe("ChoiceSourceOverlay", () => {
  it("répartit l'appel, la lecture, la mise en forme et l'essai en quatre onglets et n'applique qu'au clic sur Appliquer", async () => {
    const u = userEvent.setup();
    const onApply = vi.fn();
    render(<ChoiceSourceOverlay field={field} connections={["svc"]} onApply={onApply} onClose={() => {}} />);
    expect(screen.getByRole("dialog", { name: /Liste dynamique/ })).toBeTruthy();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Appel", "Réponse", "Mise en forme", "Essai"]);
    expect(visible("Connexion") && visible("Chemin") && !visible("Forme")).toBe(true); // premier onglet : l'appel seulement
    await u.click(tab(/Réponse/));
    expect(visible("Liste") && visible("Valeur") && !visible("Connexion")).toBe(true);
    await u.click(tab(/Mise en forme/));
    expect(["Forme", "Tri", "Garder si", "Libellé calculé"].every(visible)).toBe(true);
    await u.selectOptions(screen.getByLabelText("Forme"), "entries");
    expect(tab(/Mise en forme ✓/)).toBeTruthy(); // l'onglet montre qu'une mise en forme est réglée
    expect(onApply).not.toHaveBeenCalled(); // rien n'est appliqué avant « Appliquer »
    await u.click(tab(/Essai/));
    expect(screen.getByRole("button", { name: /Lire cette réponse/ })).toBeTruthy();
    await u.click(screen.getByRole("button", { name: "Appliquer" }));
    expect(onApply).toHaveBeenCalledWith({ ...field, source: { ...field.source, items_as: "entries" } });
  });

  it("garde la réponse d'exemple collée quand on change d'onglet", async () => {
    const u = userEvent.setup();
    render(<ChoiceSourceOverlay field={field} connections={["svc"]} onApply={() => {}} onClose={() => {}} />);
    await u.click(tab(/Essai/));
    await u.type(screen.getByLabelText("Réponse d'exemple du service (JSON)"), "abc");
    await u.click(tab(/Appel/));
    await u.click(tab(/Essai/));
    expect((screen.getByLabelText("Réponse d'exemple du service (JSON)") as HTMLTextAreaElement).value).toBe("abc");
  });

  it("refuse d'appliquer sans chemin qui commence par « / » et le signale sur l'onglet Appel", async () => {
    const u = userEvent.setup();
    render(<ChoiceSourceOverlay field={{ ...field, source: { connection: "svc", path: "politique" } }} connections={["svc"]} onApply={() => {}} onClose={() => {}} />);
    expect((screen.getByRole("button", { name: "Appliquer" }) as HTMLButtonElement).disabled).toBe(true);
    expect(tab(/Appel ⚠/)).toBeTruthy();
    await u.clear(screen.getByLabelText("Chemin"));
    await u.type(screen.getByLabelText("Chemin"), "/politique");
    expect((screen.getByRole("button", { name: "Appliquer" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("permet de faire dépendre la connexion d'un champ, parmi une liste fermée, et ajoute ce champ aux champs lus", async () => {
    const u = userEvent.setup();
    const onApply = vi.fn();
    render(<ChoiceSourceOverlay field={field} connections={["tailnet-prod", "tailnet-test"]} onApply={onApply} onClose={() => {}} />);
    expect(screen.queryByLabelText("Connexions permises")).toBeNull(); // option fermée par défaut
    await u.click(screen.getByLabelText("La connexion dépend d'un champ du formulaire"));
    expect(screen.queryByLabelText("Connexion")).toBeNull(); // la connexion fixe disparaît
    expect((screen.getByRole("button", { name: "Appliquer" }) as HTMLButtonElement).disabled).toBe(true); // ni champ ni liste : pas applicable
    await u.type(screen.getByLabelText("Champ qui choisit"), "env");
    expect((screen.getByRole("button", { name: "Appliquer" }) as HTMLButtonElement).disabled).toBe(true); // liste vide
    await u.type(screen.getByLabelText("Connexions permises"), "prod | tailnet-prod\ntest | tailnet-test");
    expect((screen.getByRole("button", { name: "Appliquer" }) as HTMLButtonElement).disabled).toBe(false);
    await u.click(screen.getByRole("button", { name: "Appliquer" }));
    expect(onApply).toHaveBeenCalledWith({
      ...field,
      source: { ...field.source, connection: "", connection_from: "env", connections: { prod: "tailnet-prod", test: "tailnet-test" }, params: ["env"] },
    });
  });

  it("revient à une connexion fixe en décochant l'option", async () => {
    const u = userEvent.setup();
    const onApply = vi.fn();
    const withChoice: FormField = { ...field, source: { connection: "", path: "/politique", params: ["env"], connection_from: "env", connections: { prod: "tailnet-prod" } } };
    render(<ChoiceSourceOverlay field={withChoice} connections={["tailnet-prod"]} onApply={onApply} onClose={() => {}} />);
    await u.click(screen.getByLabelText("La connexion dépend d'un champ du formulaire"));
    await u.click(screen.getByRole("button", { name: "Appliquer" }));
    const applied = onApply.mock.calls[0]?.[0] as FormField;
    expect(applied.source?.connection_from).toBeUndefined();
    expect(applied.source?.connections).toBeUndefined();
    expect(applied.source?.connection).toBe("tailnet-prod");
  });
});
