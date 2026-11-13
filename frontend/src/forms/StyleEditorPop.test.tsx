import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Pop } from "./StyleEditor";

function Menu() {
  return (
    <Pop label="Alignement" icon={<span>⇆</span>}>
      {(close) => (
        <>
          {["Gauche", "Centre", "Droite"].map((n, i) => (
            <button key={n} type="button" role="menuitemradio" aria-checked={i === 1} onClick={close}>
              {n}
            </button>
          ))}
        </>
      )}
    </Pop>
  );
}

describe("menu contextuel du styliste : le clavier fonctionne dans le conteneur qui relaie les touches", () => {
  it("s'ouvre sur l'élément coché, les flèches déplacent le focus avec bouclage, Échap ferme et rend le focus au bouton", async () => {
    render(<Menu />);
    const button = screen.getByRole("button", { name: "Alignement" });
    await userEvent.click(button);
    expect(screen.getByRole("menuitemradio", { name: "Centre" })).toHaveFocus(); // ouvre sur le choix courant
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("menuitemradio", { name: "Droite" })).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}"); // bouclage
    expect(screen.getByRole("menuitemradio", { name: "Gauche" })).toHaveFocus();
    await userEvent.keyboard("{ArrowUp}");
    expect(screen.getByRole("menuitemradio", { name: "Droite" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: "Alignement" })).toBeNull();
    expect(button).toHaveFocus();
  });

  it("choisir un élément referme le menu", async () => {
    render(<Menu />);
    await userEvent.click(screen.getByRole("button", { name: "Alignement" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: "Gauche" }));
    expect(screen.getByRole("button", { name: "Alignement" })).toHaveAttribute("aria-expanded", "false");
  });
});
