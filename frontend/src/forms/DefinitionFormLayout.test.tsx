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
      // biome-ignore lint/suspicious/noThenProperty: `then` est un nom du format des règles du formulaire (contrat d'API) ; ces objets sont des données, jamais attendues par `await` — acceptée le 2026-11-12 : valeur toujours un tableau (littéral ici ; donnée serveur validée par `check_rules`)
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
    // les lignes se comptent dans la pile de gauche (groupe, utilisateur, motif), à part de celle du champ haut de droite
    expect([rowOf("groupe"), rowOf("utilisateur"), rowOf("motif")]).toEqual(["1", "2", "3"]);
    await u.selectOptions(screen.getByLabelText("Action"), "retirer"); // « utilisateur » est masqué
    expect(document.querySelector('[data-key="utilisateur"]')).toBeNull();
    expect(rowOf("motif")).toBe("2"); // il prend la place de celui du dessus, malgré le champ haut de droite
    await u.selectOptions(screen.getByLabelText("Action"), "ajouter");
    expect([rowOf("utilisateur"), rowOf("motif")]).toEqual(["2", "3"]);
  });

  it("deux colonnes dont les lignes ne s'alignent pas sont deux piles : la hauteur de l'une ne déplace pas l'autre", () => {
    render(<Harness />);
    const stackOf = (key: string) => document.querySelector(`[data-key="${key}"]`)?.closest(".fb-stack");
    expect(stackOf("groupe")).toBe(stackOf("motif"));
    expect(stackOf("membres")).not.toBe(stackOf("groupe"));
    expect(document.querySelector('[data-key="action"]')?.closest(".fb-stack")).toBeNull(); // la ligne pleine largeur reste dans la grille de la section
  });

  it("des champs côte à côte sur les mêmes lignes restent alignés dans une seule grille", () => {
    const d: FormDefinition = { version: 1, sections: [{ id: "s", title: "S", fields: [
      { key: "prenom", type: "text", label: "Prénom", layout: { row: 1, col: 1, span: 6 } },
      { key: "nom", type: "text", label: "Nom", layout: { row: 1, col: 7, span: 6 } },
      { key: "ville", type: "text", label: "Ville", layout: { row: 2, col: 1, span: 6 } },
      { key: "cp", type: "text", label: "Code postal", layout: { row: 2, col: 7, span: 6 } },
    ] }] };
    render(<DefinitionForm definition={d} values={{}} onChange={() => {}} />);
    expect(document.querySelector(".fb-stack")).toBeNull();
    expect([rowOf("prenom"), rowOf("nom"), rowOf("ville"), rowOf("cp")]).toEqual(["1", "1", "2", "2"]);
  });
});
