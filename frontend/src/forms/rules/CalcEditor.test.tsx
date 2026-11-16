import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { FormField } from "../types";
import { CalcEditor } from "./CalcEditor";

const field = { key: "total", label: "Total", type: "number" } as FormField;
const others = [
  { key: "prix", label: "Prix", type: "number" },
  { key: "qte", label: "Quantité", type: "number" },
] as FormField[];

function Harness({ initial }: { initial: string }) {
  const [formula, setFormula] = useState(initial);
  return <CalcEditor field={field} others={others} formula={formula} onFormula={setFormula} />;
}

describe("Éditeur de formule : insertion d'un champ", () => {
  it("puts the caret right after the inserted text at once, so what is typed next lands after it (not after a delayed caret restore)", async () => {
    render(<Harness initial="ab" />);
    const area = screen.getByLabelText("Formule") as HTMLTextAreaElement;
    area.focus();
    area.setSelectionRange(1, 1);
    await userEvent.click(screen.getByRole("button", { name: /^Prix$/ }));
    expect(area.value).toBe("a{prix}b");
    // no animation frame has run: the caret is already where typing must continue
    expect([area.selectionStart, area.selectionEnd]).toEqual([7, 7]);
  });

  it("replaces the selected text and keeps typing in order after two insertions", async () => {
    render(<Harness initial="" />);
    const area = screen.getByLabelText("Formule") as HTMLTextAreaElement;
    await userEvent.click(screen.getByRole("button", { name: /^Prix$/ }));
    await userEvent.keyboard(" * ");
    await userEvent.click(screen.getByRole("button", { name: /^Quantité$/ }));
    await userEvent.keyboard(" * 1.2");
    expect(area.value).toBe("{prix} * {qte} * 1.2");
  });
});
