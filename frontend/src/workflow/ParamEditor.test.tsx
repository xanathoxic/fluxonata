import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { ParamEditor } from "./ParamEditor";
import type { OutputField } from "./graphOps";

const vars: OutputField[] = [
  { path: "inputs.texte", label: "Entrée « Texte »", type: "string" },
  { path: "steps.a.output.words", label: "Étape A → Mots", type: "integer" },
  { path: "steps.a.output.items", label: "Étape A → Éléments", type: "array" },
];

function Harness({ type }: { type: "string" | "integer" }) {
  const [value, setValue] = useState<unknown>(undefined);
  return (
    <>
      <ParamEditor name="p" schema={{ type, title: "Paramètre" }} required value={value} variables={vars} onChange={setValue} />
      <output aria-label="valeur">{JSON.stringify(value) ?? "undefined"}</output>
    </>
  );
}

describe("ParamEditor", () => {
  it("offers only type-compatible variables and marks the others as disabled with a reason", () => {
    render(<Harness type="integer" />);
    expect(screen.getByRole("option", { name: "Étape A → Mots" })).toBeEnabled();
    const incompatible = screen.getByRole("option", { name: /Entrée « Texte » \(type incompatible\)/ });
    expect(incompatible).toBeDisabled();
  });

  it("inserts a variable as a template and lets the user go back to a fixed value", async () => {
    render(<Harness type="string" />);
    await userEvent.selectOptions(screen.getByLabelText("Insérer une variable pour Paramètre"), "inputs.texte");
    expect(screen.getByLabelText("valeur")).toHaveTextContent('"{{ inputs.texte }}"');
    expect(screen.getByRole("textbox", { name: /Paramètre/ })).toHaveValue("{{ inputs.texte }}");
    await userEvent.click(screen.getByRole("button", { name: "Valeur fixe" }));
    expect(screen.getByLabelText("valeur")).toHaveTextContent("undefined");
  });

  it("keeps numbers typed", async () => {
    render(<Harness type="integer" />);
    await userEvent.type(screen.getByRole("spinbutton", { name: /Paramètre/ }), "42");
    expect(screen.getByLabelText("valeur")).toHaveTextContent("42");
  });
});

describe("ParamEditor : listes et objets", () => {
  function ListHarness() {
    const [value, setValue] = useState<unknown>(["Objet : {{ inputs.texte }}"]);
    return (
      <>
        <ParamEditor name="lines" schema={{ type: "array", title: "Lignes" }} required value={value} variables={vars} onChange={setValue} />
        <output aria-label="valeur">{JSON.stringify(value) ?? "undefined"}</output>
      </>
    );
  }

  it("shows a list as JSON and keeps it a list (never « [object Object] » or a string)", () => {
    render(<ListHarness />);
    expect(screen.getByLabelText(/Lignes \(JSON liste\)/)).toHaveTextContent('"Objet : {{ inputs.texte }}"');
    expect(screen.getByLabelText("valeur")).toHaveTextContent('["Objet : {{ inputs.texte }}"]');
  });

  it("still lets a variable provide the whole list", async () => {
    render(<ListHarness />);
    await userEvent.selectOptions(screen.getByLabelText("Insérer une variable pour Lignes"), "steps.a.output.items");
    expect(screen.getByLabelText("valeur")).toHaveTextContent('"{{ steps.a.output.items }}"');
    expect(screen.getByRole("textbox", { name: /Lignes/ })).toHaveValue("{{ steps.a.output.items }}");
  });
});
