import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { DefinitionForm } from "./DefinitionForm";
import type { FormDefinition } from "./types";

// colonne de gauche (« utilisateur » n'existe que pour un ajout) à côté d'un champ de 7 lignes à droite
const definition: FormDefinition = {
  version: 1,
  sections: [{ id: "s", title: "S", fields: [
    { key: "action", type: "select", label: "Action", options: [{ value: "ajouter", label: "Ajouter" }, { value: "retirer", label: "Retirer" }], layout: { row: 1, col: 1, span: 12 } },
    { key: "groupe", type: "text", label: "Groupe", layout: { row: 2, col: 1, span: 5 } },
    { key: "membres", type: "textarea", label: "Membres", layout: { row: 2, col: 6, span: 7, height: 7 } },
    { key: "utilisateur", type: "text", label: "Utilisateur", layout: { row: 3, col: 1, span: 5 },
      rules: [{ when: { field: "action", op: "equals", value: "ajouter" }, then: [{ do: "show" }], else: [{ do: "hide" }] }] },
    { key: "motif", type: "text", label: "Motif", layout: { row: 4, col: 1, span: 5 } },
  ] }],
};
const rowOf = (key: string) => /--r:\s*(\d+)/.exec(document.querySelector(`[data-key="${key}"]`)?.getAttribute("style") ?? "")?.[1];

function Harness() {
  const [values, setValues] = useState<Record<string, unknown>>({ action: "ajouter" });
  return <DefinitionForm definition={definition} values={values} onChange={setValues} />;
}

describe("mise en page : un champ masqué laisse la place", () => {
  it("fait remonter le champ du dessous, puis le remet à sa place quand le champ réapparaît", async () => {
    const u = userEvent.setup();
    render(<Harness />);
    expect([rowOf("utilisateur"), rowOf("motif")]).toEqual(["3", "4"]);
    await u.selectOptions(screen.getByLabelText("Action"), "retirer"); // « utilisateur » est masqué
    expect(document.querySelector('[data-key="utilisateur"]')).toBeNull();
    expect(rowOf("motif")).toBe("3"); // il prend la place de celui du dessus, malgré le champ haut de droite
    await u.selectOptions(screen.getByLabelText("Action"), "ajouter");
    expect([rowOf("utilisateur"), rowOf("motif")]).toEqual(["3", "4"]);
  });
});
