import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Overlay } from "../components/Overlay";
import { Palette, type PaletteItem } from "./Palette";
import { RowResizer } from "./RunSchemaView";

describe("alternatives clavier aux interactions à la souris", () => {
  it("palette : ajouter une étape au clavier (alternative au glisser-déposer sur le canevas)", async () => {
    const added: PaletteItem[] = [];
    render(<Palette actions={[]} selectedName={null} onAdd={(i) => added.push(i)} />);
    const tile = screen.getAllByRole("button", { name: /^Ajouter : / })[0] as HTMLElement;
    tile.focus();
    await userEvent.keyboard("{Enter}");
    await userEvent.keyboard(" ");
    expect(added).toHaveLength(2); // Entrée et Espace ajoutent
    expect(added[0]?.kind).toBe("generic");
  });

  it("palette : la recherche restreint les tuiles sans la souris", async () => {
    render(<Palette actions={[]} selectedName={null} onAdd={() => {}} />);
    const all = screen.getAllByRole("button", { name: /^Ajouter : / }).length;
    await userEvent.type(screen.getByLabelText("Rechercher un élément"), "SQL");
    const some = screen.getAllByRole("button", { name: /^Ajouter : / }).length;
    expect(some).toBeGreaterThan(0);
    expect(some).toBeLessThan(all);
  });

  it("surcouche : un clic sur le rideau ferme, un clic dans le panneau non, et Échap ferme au clavier", async () => {
    const onClose = vi.fn();
    const { baseElement } = render(
      <Overlay variant="modal" title="Essai" onClose={onClose}>
        <p>contenu</p>
      </Overlay>,
    );
    await userEvent.click(screen.getByText("contenu"));
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.pointer({ keys: "[MouseLeft]", target: baseElement.querySelector(".overlay-backdrop") as HTMLElement });
    expect(onClose).toHaveBeenCalledTimes(1);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("séparateur horizontal : ↑ ↓ changent la hauteur par pas, Maj : pas plus grand, Début : valeur par défaut", async () => {
    const seen: number[] = [];
    render(<RowResizer value={200} max={400} onChange={(v) => seen.push(v)} />);
    const sep = screen.getByRole("separator");
    expect(sep).toHaveAttribute("aria-orientation", "horizontal");
    sep.focus();
    await userEvent.keyboard("{ArrowUp}{ArrowDown}{Shift>}{ArrowUp}{/Shift}{Home}");
    expect(seen[0]).toBe(224);
    expect(seen[1]).toBe(176);
    expect(seen[2]).toBe(296);
    expect(seen).toHaveLength(4);
  });
});
