import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { fieldRows, parseObjectSchema, SchemaFields, withRows } from "./SchemaFields";

const SCHEMA = {
  type: "object",
  description: "Entrées.",
  required: ["n"],
  additionalProperties: false,
  properties: {
    n: { type: "integer", title: "Nombre", description: "Un nombre.", minimum: 1 },
    nom: { type: "string", title: "Nom", description: "Le nom.", enum: ["a", "b"] },
  },
};

function Harness({ kind = "in" as "in" | "out" }) {
  const [text, setText] = useState(JSON.stringify(SCHEMA, null, 2));
  return (
    <>
      <SchemaFields kind={kind} idPrefix="t" value={text} onChange={setText} />
      <pre data-testid="out">{text}</pre>
    </>
  );
}
const out = () => JSON.parse(screen.getByTestId("out").textContent ?? "{}");

describe("SchemaFields", () => {
  it("lit et réécrit les champs sans perdre le reste du schéma", () => {
    const s = parseObjectSchema(JSON.stringify(SCHEMA));
    expect(s).not.toBeNull();
    const rows = fieldRows(s ?? {});
    expect(rows.map((r) => [r.name, r.required])).toEqual([
      ["n", true],
      ["nom", false],
    ]);
    const back = withRows(s ?? {}, rows);
    expect((back as { additionalProperties?: boolean }).additionalProperties).toBe(false);
    expect(back.description).toBe("Entrées.");
    const nom = back.properties?.nom as { enum?: unknown[] } | undefined;
    expect(nom?.enum).toEqual(["a", "b"]);
    expect(parseObjectSchema("pas du json")).toBeNull();
    expect(parseObjectSchema("[1]")).toBeNull();
  });

  it("modifier le titre, le type et le caractère obligatoire garde enum et bornes", async () => {
    render(<Harness />);
    const u = userEvent.setup();
    await u.type(screen.getByLabelText("Titre de n"), "!");
    await u.selectOptions(screen.getByLabelText("Type de nom"), "integer");
    await u.click(screen.getByLabelText("nom : obligatoire"));
    const s = out();
    expect(s.properties.n.title).toBe("Nombre!");
    expect(s.properties.n.minimum).toBe(1);
    expect(s.properties.nom.type).toBe("integer");
    expect(s.properties.nom.enum).toEqual(["a", "b"]);
    expect(s.required).toEqual(["n", "nom"]);
  });

  it("ajoute, renomme (sans doublon ni caractère interdit) et supprime un champ", async () => {
    render(<Harness />);
    const u = userEvent.setup();
    await u.click(screen.getByRole("button", { name: "Ajouter une entrée" }));
    expect(Object.keys(out().properties)).toEqual(["n", "nom", "champ_3"]);
    await u.clear(screen.getByLabelText("Identifiant de l'entrée 3"));
    expect(Object.keys(out().properties)).toContain("champ_3"); // vide : refusé
    expect((screen.getByLabelText("Identifiant de l'entrée 3") as HTMLInputElement).value).toBe(""); // on peut tout effacer avant de retaper
    await u.type(screen.getByLabelText("Identifiant de l'entrée 3"), "a b");
    expect(Object.keys(out().properties)).toContain("a_b");
    await u.clear(screen.getByLabelText("Identifiant de l'entrée 3"));
    await u.type(screen.getByLabelText("Identifiant de l'entrée 3"), "n"); // déjà pris : refusé, rétabli en quittant le champ
    await u.tab();
    expect(Object.keys(out().properties)).toEqual(["n", "nom", "a_b"]);
    expect((screen.getByLabelText("Identifiant de l'entrée 3") as HTMLInputElement).value).toBe("a_b");
    await u.click(screen.getByRole("button", { name: "Supprimer l'entrée n" }));
    expect(out().required).toBeUndefined();
    expect(Object.keys(out().properties)).toEqual(["nom", "a_b"]);
  });

  it("la valeur par défaut est typée ; une sortie n'a pas de colonne de valeur par défaut", async () => {
    const { unmount } = render(<Harness />);
    const u = userEvent.setup();
    await u.type(screen.getByLabelText("Valeur par défaut de n"), "5");
    await u.tab();
    expect(out().properties.n.default).toBe(5);
    unmount();
    render(<Harness kind="out" />);
    expect(screen.queryByLabelText("Valeur par défaut de n")).toBeNull();
    expect(screen.getByRole("columnheader", { name: "Toujours présente" })).toBeTruthy();
  });

  it("un JSON illisible laisse le texte brut avec un message", () => {
    render(<SchemaFields kind="in" idPrefix="t" value="{" onChange={() => {}} />);
    expect(screen.getByRole("alert").textContent).toMatch(/pas lisible/);
    expect(screen.getByLabelText(/Schéma des entrées/)).toBeTruthy();
  });
});
