import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { useRadioGroup } from "./useRadioGroup";

function Segments({ disabledOu = false }: { disabledOu?: boolean }) {
  const [op, setOp] = useState("all");
  const { ref, onKeyDown } = useRadioGroup<HTMLSpanElement>();
  const b = (id: string, label: string, disabled = false) => (
    // biome-ignore lint/a11y/useSemanticElements: harnais d'essai : il reproduit exactement le motif de bouton `role="radio"` que le crochet doit gérer au clavier
    <button type="button" role="radio" aria-checked={op === id} disabled={disabled} onClick={() => setOp(id)}>
      {label}
    </button>
  );
  return (
    <>
      <button type="button">avant</button>
      <span ref={ref} onKeyDown={onKeyDown} role="radiogroup" aria-label="Combinaison">
        {b("all", "ET")}
        {b("any", "OU", disabledOu)}
        {b("none", "NI")}
      </span>
      <button type="button">après</button>
    </>
  );
}

const checked = () => screen.getAllByRole("radio").filter((r) => r.getAttribute("aria-checked") === "true").map((r) => r.textContent);

describe("groupe radio au clavier", () => {
  it("un seul arrêt de tabulation : le choix courant ; on sort du groupe avec Tab", async () => {
    render(<Segments />);
    await userEvent.tab(); // « avant »
    await userEvent.tab();
    expect(screen.getByRole("radio", { name: "ET" })).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByText("après")).toHaveFocus(); // les autres boutons du groupe ne sont pas des arrêts de tabulation
  });

  it("les flèches déplacent le focus et le choix, avec bouclage ; Début et Fin", async () => {
    render(<Segments />);
    screen.getByRole("radio", { name: "ET" }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(checked()).toEqual(["OU"]);
    expect(screen.getByRole("radio", { name: "OU" })).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}{ArrowRight}"); // NI puis bouclage vers ET
    expect(checked()).toEqual(["ET"]);
    await userEvent.keyboard("{ArrowLeft}"); // bouclage vers NI
    expect(checked()).toEqual(["NI"]);
    await userEvent.keyboard("{Home}");
    expect(checked()).toEqual(["ET"]);
    await userEvent.keyboard("{End}");
    expect(checked()).toEqual(["NI"]);
  });

  it("saute un bouton désactivé et le tabindex suit le choix", async () => {
    render(<Segments disabledOu />);
    screen.getByRole("radio", { name: "ET" }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(checked()).toEqual(["NI"]); // « OU » est désactivé
    expect(screen.getByRole("radio", { name: "NI" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("radio", { name: "ET" })).toHaveAttribute("tabindex", "-1");
  });

  it("une autre touche ne fait rien", async () => {
    render(<Segments />);
    screen.getByRole("radio", { name: "ET" }).focus();
    await userEvent.keyboard("a");
    expect(checked()).toEqual(["ET"]);
  });
});
