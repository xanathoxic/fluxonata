import { render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { DefinitionForm } from "./DefinitionForm";
import type { FormDefinition } from "./types";

function Harness({ definition }: { definition: FormDefinition }) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  return <DefinitionForm definition={definition} values={values} onChange={setValues} />;
}

const OPTIONS = [{ value: "a", label: "Alpha" }, { value: "b", label: "Bravo" }];
const custom = (key: string, label: string, spec: Record<string, unknown>) => ({ key, type: "custom", label, control: { ref: key, version: "1.0.0", name: label, spec: { version: 1, ...spec } } });
const definition = (...fields: unknown[]): FormDefinition => ({ version: 1, sections: [{ id: "s", title: "S", fields: fields as never }] });

describe("groupes nommés : le nom accessible du groupe est le libellé du champ", () => {
  it("cases à cocher et boutons radio : groupe ou groupe radio nommé par le libellé", () => {
    render(<Harness definition={definition({ key: "cases", type: "checkbox_list", label: "Membres de l'équipe", options: OPTIONS }, { key: "mode", type: "radio", label: "Mode de livraison", options: OPTIONS })} />);
    expect(screen.getByRole("group", { name: "Membres de l'équipe" })).toBeTruthy();
    expect(screen.getByRole("radiogroup", { name: "Mode de livraison" })).toBeTruthy();
  });

  it("contrôle personnalisé : le groupe porte le libellé du champ, ses champs internes restent nommés", () => {
    render(<Harness definition={definition(custom("contact", "Personne à contacter", { mode: "group", parts: [{ key: "nom", label: "Nom", type: "text" }] }))} />);
    const group = screen.getByRole("group", { name: "Personne à contacter" });
    expect(within(group).getByLabelText("Nom")).toBeTruthy();
  });

  it("liste double : le groupe est nommé par le libellé, et ses deux listes par leurs titres", () => {
    render(<Harness definition={definition(custom("equipes", "Équipes concernées", { mode: "items", parts: [], items: OPTIONS, display: "dual" }))} />);
    const group = screen.getByRole("group", { name: "Équipes concernées" });
    expect(within(group).getByRole("listbox", { name: "Disponibles" })).toBeTruthy();
    expect(within(group).getByRole("listbox", { name: "Sélectionnés" })).toBeTruthy();
  });
});
