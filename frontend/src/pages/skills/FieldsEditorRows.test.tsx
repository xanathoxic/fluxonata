import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { FieldsEditor } from "./FieldsEditor";
import type { Field } from "./fields";

const row = (name: string): Field => ({ name, type: "string", required: false, description: `description de ${name}` });

function Host({ start }: { start: string[] }) {
  const [fields, setFields] = useState<Field[]>(start.map(row));
  return <FieldsEditor legend="Champs" fields={fields} onChange={setFields} withRequired={false} minRows={0} idPrefix="t" addLabel="Ajouter un champ" />;
}
const names = () => screen.getAllByLabelText(/^Nom du champ/).map((i) => (i as HTMLInputElement).value);

describe("lignes éditables sans identifiant propre (clé d'index) : ce que les essais établissent", () => {
  it("retirer une ligne du milieu conserve les valeurs des autres lignes, dans l'ordre", async () => {
    render(<Host start={["a", "b", "c", "d"]} />);
    await userEvent.click(screen.getByRole("button", { name: "Retirer le champ b" }));
    expect(names()).toEqual(["a", "c", "d"]);
    expect((screen.getAllByPlaceholderText("Description (facultatif)")[1] as HTMLInputElement).value).toBe("description de c");
  });

  it("insérer une ligne en fin conserve les lignes existantes ; la saisie dans une ligne ne touche pas les autres", async () => {
    render(<Host start={["a", "b"]} />);
    await userEvent.click(screen.getByRole("button", { name: "Ajouter un champ" }));
    expect(names()).toEqual(["a", "b", "champ_3"]);
    await userEvent.type(screen.getAllByLabelText(/^Nom du champ/)[1] as HTMLElement, "x");
    expect(names()).toEqual(["a", "bx", "champ_3"]);
  });

  it("au clavier, après avoir retiré une ligne, le focus reste à la même position (bouton de la ligne suivante) : il n'est pas perdu", async () => {
    render(<Host start={["a", "b", "c"]} />);
    const second = screen.getByRole("button", { name: "Retirer le champ b" });
    second.focus();
    await userEvent.keyboard("{Enter}");
    expect(names()).toEqual(["a", "c"]);
    expect(document.activeElement).not.toBe(document.body); // avec une identité par ligne, la ligne retirée emporterait son bouton : focus perdu sans gestion dédiée
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Retirer le champ c" }));
  });
});
