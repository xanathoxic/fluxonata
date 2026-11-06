import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { DefinitionForm } from "./DefinitionForm";
import type { FormDefinition } from "./types";

const definition: FormDefinition = {
  version: 1,
  sections: [{ id: "s", title: "S", fields: [
    { key: "mode", type: "select", label: "Mode", options: [{ value: "edit", label: "Modifier" }, { value: "lock", label: "Verrouiller" }] },
    { key: "nom", type: "text", label: "Nom", rules: [{ when: { field: "mode", op: "equals", value: "lock" }, then: [{ do: "readonly" }], else: [{ do: "editable" }] }] },
    { key: "choix", type: "radio", label: "Choix", options: [{ value: "a", label: "A" }, { value: "b", label: "B" }], rules: [{ when: { field: "mode", op: "equals", value: "lock" }, then: [{ do: "readonly" }] }] },
  ] }],
};
function Harness() {
  const [values, setValues] = useState<Record<string, unknown>>({ nom: "Valeur", choix: "a" });
  return <DefinitionForm definition={definition} values={values} onChange={setValues} />;
}

describe("lecture seule par une règle", () => {
  it("verrouille le champ (avec sa valeur) tant que la règle s'applique, puis le rend modifiable", async () => {
    const u = userEvent.setup();
    render(<Harness />);
    const nom = screen.getByLabelText("Nom") as HTMLInputElement;
    expect(nom.disabled).toBe(false);
    await u.selectOptions(screen.getByLabelText("Mode"), "lock");
    expect(nom.disabled).toBe(true);
    expect(nom.value).toBe("Valeur"); // la valeur reste affichée
    expect(screen.getAllByRole("radio").every((r) => (r as HTMLInputElement).disabled)).toBe(true); // aussi pour les boutons radio
    await u.selectOptions(screen.getByLabelText("Mode"), "edit");
    expect(nom.disabled).toBe(false);
  });
});
