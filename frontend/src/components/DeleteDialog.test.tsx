import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { DeleteDialog, type Usage } from "./DeleteDialog";

const api = vi.fn();
vi.mock("../api", async (orig) => ({ ...(await orig<typeof import("../api")>()), api: (...a: unknown[]) => api(...a) }));

const FREE: Usage = { used_by: [], hidden: 0, total: 0 };
const USED: Usage = {
  used_by: [
    { slug: "parent", name: "Parent", where: ["brouillon", "v1.0.0"] },
    { slug: "autre", name: "Autre", where: ["v2.0.0"] },
  ],
  hidden: 1,
  total: 3,
};

function show(props: Partial<Parameters<typeof DeleteDialog>[0]> = {}) {
  const onClose = vi.fn();
  const onDeleted = vi.fn();
  const ctx = { workspace: { slug: "team-a" }, can: () => true } as unknown as Ctx;
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AppContext.Provider value={ctx}>
        <MemoryRouter>
          <DeleteDialog kind="workflow" slug="enfant" name="Enfant" onClose={onClose} onDeleted={onDeleted} {...props} />
        </MemoryRouter>
      </AppContext.Provider>
    </QueryClientProvider>,
  );
  return { onClose, onDeleted };
}
const deletes = () => api.mock.calls.filter((c) => (c[1] as { method?: string } | undefined)?.method === "DELETE");

describe("Suppression d'une action ou d'un workflow", () => {
  beforeEach(() => {
    api.mockReset();
  });

  it("utilisé : liste les workflows (brouillon / versions), les workflows cachés, et ne propose aucune suppression", async () => {
    api.mockResolvedValue(USED);
    const { onClose } = show();
    await screen.findByText(/Suppression impossible : 3 workflows appellent le workflow\./); // la fenêtre de chargement porte déjà un dialogue : on attend le contenu
    const dialog = screen.getByRole("dialog");
    const list = within(dialog).getByRole("list", { name: "Workflows qui l'utilisent" });
    expect(within(list).getByRole("link", { name: "Parent" })).toHaveAttribute("href", "/workflows/parent/schema");
    expect(within(list).getByText("(brouillon, v1.0.0)")).toBeInTheDocument();
    expect(within(dialog).getByText(/1 autre workflow que vous n'avez pas le droit de voir l'utilise aussi/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Supprimer" })).toBeNull();
    await userEvent.click(within(dialog).getAllByRole("button", { name: "Fermer" }).at(-1) as HTMLElement); // le bouton du pied (l'autre est l'icône de l'en-tête)
    expect(onClose).toHaveBeenCalled();
    expect(deletes()).toHaveLength(0);
  });

  it("inutilisé : demande confirmation puis supprime (rien n'est supprimé avant le clic)", async () => {
    api.mockImplementation(async (_path: string, opts?: { method?: string }) => (opts?.method === "DELETE" ? undefined : FREE));
    const { onDeleted } = show({ kind: "action", slug: "ping", name: "Ping" });
    await screen.findByText(/Aucun workflow n'utilise l'action/);
    const dialog = screen.getByRole("dialog", { name: /Supprimer « Ping » \?/ });
    expect(deletes()).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Supprimer" }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
    expect(deletes()).toHaveLength(1);
    expect(deletes()[0]?.[0]).toBe("/workspaces/team-a/actions/ping/delete/");
  });

  it("une action appelée seulement par d'anciennes versions : suppression permise, avec la liste de ce qui ne s'exécutera plus", async () => {
    const OLD: Usage = { ...FREE, older_versions: [{ slug: "parent", name: "Parent", versions: ["v1.0.0", "v1.1.0"] }] };
    api.mockImplementation(async (_path: string, opts?: { method?: string }) => (opts?.method === "DELETE" ? undefined : OLD));
    const { onDeleted } = show({ kind: "action", slug: "ping", name: "Ping" });
    const note = await screen.findByRole("note", { name: "Anciennes versions concernées" });
    expect(note).toHaveTextContent("ne pourront plus s'exécuter");
    expect(note).toHaveTextContent("Parent");
    expect(note).toHaveTextContent("v1.0.0, v1.1.0");
    expect(deletes()).toHaveLength(0);
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Supprimer" }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
  });

  it("devenu utilisé entre-temps (409) : la liste s'affiche à la place, rien n'est supprimé", async () => {
    let calls = 0;
    api.mockImplementation(async (_path: string, opts?: { method?: string }) => {
      if (opts?.method === "DELETE") throw Object.assign(new Error("utilisé"), { status: 409 });
      calls += 1;
      return calls === 1 ? FREE : USED;
    });
    const { onDeleted } = show();
    await userEvent.click(await screen.findByRole("button", { name: "Supprimer" }));
    expect(await screen.findByText(/Suppression impossible : 3 workflows/)).toBeInTheDocument();
    expect(onDeleted).not.toHaveBeenCalled();
  });

  it("un autre refus (déjà exécuté…) est expliqué et la fenêtre reste ouverte", async () => {
    api.mockImplementation(async (_path: string, opts?: { method?: string }) => {
      if (opts?.method === "DELETE")
        throw Object.assign(new Error("refusé"), { status: 400, details: { workflow: "Suppression refusée : il a déjà été exécuté." } });
      return FREE;
    });
    const { onDeleted } = show();
    await userEvent.click(await screen.findByRole("button", { name: "Supprimer" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("déjà été exécuté");
    expect(onDeleted).not.toHaveBeenCalled();
  });

  it("si l'usage ne peut pas être lu, ne propose pas de supprimer à l'aveugle", async () => {
    api.mockRejectedValue(Object.assign(new Error("panne"), { status: 500 }));
    show();
    expect(await screen.findByRole("alert")).toHaveTextContent("Impossible de vérifier");
    expect(screen.queryByRole("button", { name: "Supprimer" })).toBeNull();
  });

  describe("avec historique et déclencheurs (confirmations expresses)", () => {
    const factsOf = (extra: Partial<Usage>): Usage => ({ ...FREE, executions: 0, running: 0, triggers: {}, ...extra });
    const serveFacts = (facts: Usage) =>
      api.mockImplementation(async (_path: string, opts?: { method?: string }) => (opts?.method === "DELETE" ? undefined : facts));
    const confirm = () => screen.getByRole("button", { name: "Supprimer" });

    it("un workflow exécuté : Supprimer reste grisé tant que l'historique n'est pas accepté ET le nom saisi ; l'adresse porte with_history", async () => {
      serveFacts(factsOf({ executions: 12 }));
      const { onDeleted } = show();
      await screen.findByText(/Mais des éléments en dépendent/);
      expect(confirm()).toBeDisabled();
      await userEvent.click(screen.getByLabelText(/Supprimer aussi l'historique : 12 exécutions/));
      expect(confirm()).toBeDisabled(); // le nom n'est pas encore saisi
      await userEvent.type(screen.getByLabelText(/saisissez le nom exact : Enfant/), "Enfan");
      expect(confirm()).toBeDisabled();
      await userEvent.type(screen.getByLabelText(/saisissez le nom exact/), "t");
      expect(confirm()).toBeEnabled();
      await userEvent.click(confirm());
      await waitFor(() => expect(onDeleted).toHaveBeenCalled());
      expect(deletes()[0]?.[0]).toBe("/workspaces/team-a/workflows/enfant/delete/?with_history=1");
    });

    it("des déclencheurs : détaillés par nature, à accepter, et l'adresse porte with_triggers (sans saisie du nom)", async () => {
      serveFacts(factsOf({ triggers: { schedules: 2, webhooks: 1, forms: 0, polls: 0 } }));
      show();
      await screen.findByText(/Mais des éléments en dépendent/);
      expect(screen.getByLabelText(/2 planifications, 1 webhook\./)).toBeInTheDocument();
      expect(confirm()).toBeDisabled();
      await userEvent.click(screen.getByLabelText(/Supprimer aussi les déclencheurs/));
      expect(screen.queryByLabelText(/saisissez le nom exact/)).toBeNull(); // seul l'historique demande de taper le nom
      await userEvent.click(confirm());
      await waitFor(() => expect(deletes()).toHaveLength(1));
      expect(deletes()[0]?.[0]).toBe("/workspaces/team-a/workflows/enfant/delete/?with_triggers=1");
    });

    it("les deux ensemble : les deux accords sont exigés séparément", async () => {
      serveFacts(factsOf({ executions: 3, triggers: { polls: 1 } }));
      show();
      await screen.findByText(/Mais des éléments en dépendent/);
      await userEvent.click(screen.getByLabelText(/Supprimer aussi l'historique/));
      await userEvent.type(screen.getByLabelText(/saisissez le nom exact/), "Enfant");
      expect(confirm()).toBeDisabled(); // les déclencheurs ne sont pas acceptés
      await userEvent.click(screen.getByLabelText(/Supprimer aussi les déclencheurs : 1 interrogation/));
      expect(confirm()).toBeEnabled();
      await userEvent.click(confirm());
      await waitFor(() => expect(deletes()).toHaveLength(1));
      expect(deletes()[0]?.[0]).toBe("/workspaces/team-a/workflows/enfant/delete/?with_history=1&with_triggers=1");
    });

    it("une action exécutée : seul l'historique est proposé ; des exécutions en cours bloquent tout, sans bouton de suppression", async () => {
      serveFacts(factsOf({ executions: 5 }));
      show({ kind: "action", slug: "ping", name: "Ping" });
      await screen.findByText(/Mais des éléments en dépendent/);
      expect(screen.queryByLabelText(/déclencheurs/)).toBeNull();
      expect(screen.getByLabelText(/Supprimer aussi l'historique : 5 exécutions/)).toBeInTheDocument();
    });

    it("des exécutions en cours : message clair, aucune suppression possible", async () => {
      serveFacts(factsOf({ executions: 4, running: 2 }));
      show();
      await screen.findByText(/2 exécutions sont encore en cours/);
      expect(screen.queryByRole("button", { name: "Supprimer" })).toBeNull();
      expect(screen.getByText(/on ne supprime jamais ce qui tourne/)).toBeInTheDocument();
      expect(deletes()).toHaveLength(0);
    });

    it("sans historique ni déclencheur, le comportement reste celui d'avant (aucun drapeau)", async () => {
      serveFacts(factsOf({}));
      show();
      await screen.findByText(/La suppression est/);
      await userEvent.click(confirm());
      await waitFor(() => expect(deletes()).toHaveLength(1));
      expect(deletes()[0]?.[0]).toBe("/workspaces/team-a/workflows/enfant/delete/");
    });
  });
});
