import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { DefinitionForm } from "./DefinitionForm";
import type { FormDefinition } from "./types";

// « utilisateur » n'existe que pour un ajout ; sinon la règle le MASQUE et le VIDE (effet « remplir » avec une valeur vide)
const definition: FormDefinition = {
  version: 1,
  sections: [{ id: "s", title: "S", fields: [
    { key: "action", type: "select", label: "Action", options: [{ value: "ajouter", label: "Ajouter" }, { value: "retirer", label: "Retirer" }] },
    { key: "utilisateur", type: "email", label: "Utilisateur",
      rules: [{ when: { field: "action", op: "equals", value: "ajouter" }, then: [{ do: "show" }, { do: "require" }], else: [{ do: "hide" }, { do: "fill", value: "" }] }] },
  ] }],
};
function Harness() {
  const [values, setValues] = useState<Record<string, unknown>>({ action: "ajouter" });
  return (<>
    <DefinitionForm definition={definition} values={values} onChange={setValues} />
    <output aria-label="valeurs">{JSON.stringify(values)}</output>
  </>);
}
const shown = () => JSON.parse(screen.getByLabelText("valeurs").textContent ?? "{}") as Record<string, unknown>;

describe("« remplir le champ » avec une valeur vide sur un champ qui se masque", () => {
  it("vide ce qui avait été saisi : en revenant, le champ est vide", async () => {
    const u = userEvent.setup();
    render(<Harness />);
    await u.type(screen.getByLabelText(/Utilisateur/), "alice@exemple.fr");
    expect(shown().utilisateur).toBe("alice@exemple.fr");
    await u.selectOptions(screen.getByLabelText("Action"), "retirer"); // le champ est masqué ET vidé
    expect(screen.queryByLabelText(/Utilisateur/)).toBeNull();
    expect(shown().utilisateur).toBe("");
    await u.selectOptions(screen.getByLabelText("Action"), "ajouter");
    expect((screen.getByLabelText(/Utilisateur/) as HTMLInputElement).value).toBe("");
  });
});
