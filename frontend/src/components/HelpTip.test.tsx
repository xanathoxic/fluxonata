import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { HelpTip } from "./HelpTip";

describe("HelpTip", () => {
  it("cache l'aide, l'ouvre au clic et la ferme avec Échap", async () => {
    render(<HelpTip>Texte d'aide</HelpTip>);
    expect(screen.queryByText("Texte d'aide")).toBeNull();
    const b = screen.getByRole("button", { name: "Aide" });
    expect(b).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(b);
    expect(screen.getByRole("note")).toHaveTextContent("Texte d'aide");
    expect(b).toHaveAttribute("aria-expanded", "true");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByText("Texte d'aide")).toBeNull();
  });
  it("se ferme au clic ailleurs et accepte un libellé", async () => {
    render(<><HelpTip caption="À savoir">Détail</HelpTip><p>ailleurs</p></>);
    await userEvent.click(screen.getByRole("button", { name: /À savoir/ }));
    expect(screen.getByRole("note")).toBeVisible();
    await userEvent.click(screen.getByText("ailleurs"));
    expect(screen.queryByRole("note")).toBeNull();
  });
});
