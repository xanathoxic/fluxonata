import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { RenameButton, RenameOverlay, type RenameKind } from "./RenameOverlay";

const api = vi.fn();
vi.mock("../api", async (orig) => ({ ...(await orig<typeof import("../api")>()), api: (...a: unknown[]) => api(...a) }));

function show(ui: React.ReactNode, can: (p: string) => boolean = () => true) {
  const ctx = { workspace: { slug: "team-a" }, can } as unknown as Ctx;
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <AppContext.Provider value={ctx}>{ui}</AppContext.Provider>
    </QueryClientProvider>,
  );
}

describe("renommer", () => {
  beforeEach(() => {
    api.mockReset().mockResolvedValue({});
  });

  it.each<[RenameKind, string]>([
    ["workflow", "/workspaces/team-a/workflows/mon-wf/meta/"],
    ["action", "/workspaces/team-a/actions/mon-wf/meta/"],
    ["skill", "/workspaces/team-a/ai/skills/mon-wf/"],
    ["form", "/workspaces/team-a/form-library/mon-wf/"],
    ["control", "/workspaces/team-a/control-library/mon-wf/"],
  ])("envoie seulement le nouveau nom au bon point d'API (%s)", async (kind, path) => {
    const onClose = vi.fn();
    show(<RenameOverlay kind={kind} slug="mon-wf" name="Ancien" onClose={onClose} />);
    const input = screen.getByLabelText("Nouveau nom");
    await userEvent.clear(input);
    await userEvent.type(input, "  Nouveau  ");
    await userEvent.click(screen.getByRole("button", { name: "Renommer" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith(path, { method: "PATCH", json: { name: "Nouveau" } }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("refuse un nom vide, inchangé ou trop long", async () => {
    show(<RenameOverlay kind="skill" slug="s" name="Ancien" onClose={() => {}} />);
    const ok = screen.getByRole("button", { name: "Renommer" });
    expect(ok).toBeDisabled(); // inchangé
    const input = screen.getByLabelText("Nouveau nom");
    await userEvent.clear(input);
    expect(ok).toBeDisabled(); // vide
    await userEvent.type(input, "x".repeat(161));
    expect(ok).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("160");
    expect(api).not.toHaveBeenCalled();
  });

  it("affiche l'erreur du serveur et laisse la fenêtre ouverte", async () => {
    api.mockRejectedValue(Object.assign(new Error("Nom déjà pris."), { status: 400 }));
    const onClose = vi.fn();
    show(<RenameOverlay kind="form" slug="f" name="A" onClose={onClose} />);
    await userEvent.type(screen.getByLabelText("Nouveau nom"), "b");
    await userEvent.click(screen.getByRole("button", { name: "Renommer" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Nom déjà pris.");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("le bouton de liste n'existe que pour qui peut modifier", async () => {
    show(<RenameButton kind="workflow" slug="w" name="W" />, (p) => p !== "edit");
    expect(screen.queryByRole("button", { name: /Renommer/ })).toBeNull();
  });

  it("le bouton de liste ouvre la fenêtre", async () => {
    show(<RenameButton kind="action" slug="a" name="Mon action" />);
    await userEvent.click(screen.getByRole("button", { name: "Renommer l'action Mon action" }));
    expect(screen.getByLabelText("Nouveau nom")).toHaveValue("Mon action");
  });
});
