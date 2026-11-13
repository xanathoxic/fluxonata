import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { OptionPicker } from "./OptionPicker";

const ITEMS = [{ value: "a", label: "Alpha" }, { value: "b", label: "Bravo" }, { value: "c", label: "Charlie", disabled: true }];

function Harness({ multiple = true }: { multiple?: boolean }) {
  const [sel, setSel] = useState<string[]>([]);
  return (
    <>
      <OptionPicker id="p" items={ITEMS} selected={sel} multiple={multiple} onToggle={(v, on) => setSel((s) => (multiple ? (on ? [...s, v] : s.filter((x) => x !== v)) : [v]))} />
      <button type="button">ailleurs</button>
      <output aria-label="choix">{sel.join(",")}</output>
    </>
  );
}
const combo = () => screen.getByRole("combobox");

describe("sélecteur d'options au clavier (conteneur qui relaie focus et touches)", () => {
  it("le focus ouvre la liste ; Tab entre dans les options ; Espace coche ; Échap referme", async () => {
    render(<Harness />);
    await userEvent.tab();
    expect(combo()).toHaveFocus();
    expect(combo()).toHaveAttribute("aria-expanded", "true");
    await userEvent.tab(); // première option
    expect(screen.getByRole("checkbox", { name: "Alpha" })).toHaveFocus();
    await userEvent.keyboard(" ");
    expect(screen.getByLabelText("choix").textContent).toBe("a");
    await userEvent.keyboard("{Escape}");
    expect(combo()).toHaveAttribute("aria-expanded", "false");
  });

  it("quitter le sélecteur au clavier referme la liste ; une option désactivée n'est pas cochable", async () => {
    render(<Harness />);
    await userEvent.tab();
    expect(screen.getByRole("checkbox", { name: "Charlie" })).toBeDisabled();
    await userEvent.tab({ shift: false }); // Alpha
    await userEvent.tab(); // Bravo
    await userEvent.tab(); // sort : bouton « ailleurs » (Charlie est désactivée)
    expect(screen.getByText("ailleurs")).toHaveFocus();
    expect(combo()).toHaveAttribute("aria-expanded", "false");
  });

  it("choix unique : un choix referme la liste", async () => {
    render(<Harness multiple={false} />);
    await userEvent.tab();
    await userEvent.tab();
    await userEvent.keyboard(" ");
    expect(screen.getByLabelText("choix").textContent).toBe("a");
    expect(combo()).toHaveAttribute("aria-expanded", "false");
  });
});
