import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfirmDialog } from "./ConfirmDialog";

describe("ConfirmDialog", () => {
  it("names the object, puts the verb on a red button, and cancels with Escape", async () => {
    const ok = vi.fn(), cancel = vi.fn();
    render(<ConfirmDialog title="Supprimer « Demande » ?" confirmLabel="Supprimer" onConfirm={ok} onCancel={cancel}><p>Les exécutions restent.</p></ConfirmDialog>);
    expect(screen.getByRole("dialog", { name: "Supprimer « Demande » ?" })).toBeInTheDocument();
    const go = screen.getByRole("button", { name: "Supprimer" });
    expect(go).toHaveClass("danger");
    await userEvent.keyboard("{Escape}");
    expect(cancel).toHaveBeenCalledOnce();
    await userEvent.click(go);
    expect(ok).toHaveBeenCalledOnce();
  });

  it("is a plain primary button when nothing is destroyed", () => {
    render(<ConfirmDialog title="Déplacer ?" confirmLabel="Déplacer" danger={false} onConfirm={() => undefined} onCancel={() => undefined}>x</ConfirmDialog>);
    expect(screen.getByRole("button", { name: "Déplacer" })).toHaveClass("primary");
  });
});
