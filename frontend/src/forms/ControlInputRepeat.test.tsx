import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { ControlInput } from "./ControlInput";
import type { FormField } from "./types";

const ADDRESS = {
  version: 1,
  mode: "group",
  parts: [
    { key: "rue", label: "Rue", type: "text" },
    { key: "ville", label: "Ville", type: "text" },
  ],
};
const spec = (extra: Record<string, unknown> = {}) => ({
  version: 1,
  mode: "repeat",
  row_label: "Invité",
  parts: [
    { key: "nom", label: "Nom", type: "text" },
    { key: "adresse", label: "Adresse", type: "custom", control: { ref: "adresse", spec: ADDRESS } },
  ],
  ...extra,
});
const field = (extra: Record<string, unknown> = {}) =>
  ({ key: "invites", type: "custom", label: "Invités", control: { ref: "invites", spec: spec(extra) } }) as unknown as FormField;

let latest: unknown;
function Host({ f, initial }: { f: FormField; initial: unknown }) {
  const [v, setV] = useState<unknown>(initial);
  latest = v;
  return <ControlInput field={f} id="c" value={v} onChange={setV} />;
}
const rows = () => screen.getAllByRole("group", { name: /^Invité \d+$/ });

describe("Lignes répétées : dupliquer une ligne", () => {
  it("ajoute juste en dessous une ligne avec les mêmes valeurs saisies, sous-contrôle compris", async () => {
    const initial = [
      { nom: "Ada", adresse: { rue: "1 rue A", ville: "Paris" } },
      { nom: "Bob", adresse: { rue: "2 rue B", ville: "Lyon" } },
    ];
    render(<Host f={field()} initial={initial} />);
    expect(rows()).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "Dupliquer invité 1" }));
    expect(rows()).toHaveLength(3);
    expect(latest).toEqual([initial[0], initial[0], initial[1]]); // la copie vient juste après l'original, l'autre ligne reste en dernier
    expect(within(rows()[1] as HTMLElement).getByLabelText("Nom")).toHaveValue("Ada");
  });

  it("la copie est indépendante : modifier l'une ne change pas l'autre", async () => {
    render(<Host f={field()} initial={[{ nom: "Ada", adresse: { rue: "1 rue A", ville: "Paris" } }]} />);
    await userEvent.click(screen.getByRole("button", { name: "Dupliquer invité 1" }));
    const second = within(rows()[1] as HTMLElement);
    await userEvent.clear(second.getByLabelText("Nom"));
    await userEvent.type(second.getByLabelText("Nom"), "Clara");
    await userEvent.clear(second.getByLabelText("Rue"));
    await userEvent.type(second.getByLabelText("Rue"), "9 rue C");
    expect(latest).toEqual([
      { nom: "Ada", adresse: { rue: "1 rue A", ville: "Paris" } },
      { nom: "Clara", adresse: { rue: "9 rue C", ville: "Paris" } },
    ]);
  });

  it("s'arrête au nombre maximal de lignes : le bouton est désactivé et rien n'est ajouté", async () => {
    render(<Host f={field({ max: 2 })} initial={[{ nom: "Ada" }, { nom: "Bob" }]} />);
    const dup = screen.getByRole("button", { name: "Dupliquer invité 2" });
    expect(dup).toBeDisabled();
    await userEvent.click(dup);
    expect(rows()).toHaveLength(2);
  });

  it("existe aussi pour l'affichage en tableau", async () => {
    render(
      <Host
        f={field({
          display: "table",
          parts: [
            { key: "nom", label: "Nom", type: "text" },
            { key: "age", label: "Âge", type: "number" },
          ],
        })}
        initial={[{ nom: "Ada", age: 36 }]}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Dupliquer invité 1" }));
    expect(latest).toEqual([
      { nom: "Ada", age: 36 },
      { nom: "Ada", age: 36 },
    ]);
  });

  it("n'apparaît pas en lecture seule", () => {
    render(<ControlInput field={field()} id="c" value={[{ nom: "Ada" }]} onChange={() => undefined} disabled />);
    expect(screen.queryByRole("button", { name: /Dupliquer/ })).toBeNull();
  });

  it("une ligne vide se duplique en ligne vide", async () => {
    render(<Host f={field()} initial={[{}]} />);
    await userEvent.click(screen.getByRole("button", { name: "Dupliquer invité 1" }));
    expect(latest).toEqual([{}, {}]);
  });
});

describe("Lignes répétées : replier et déplier", () => {
  const two = [
    { nom: "Ada", adresse: { rue: "1 rue A", ville: "Paris" } },
    { nom: "Bob", adresse: { rue: "2 rue B", ville: "Lyon" } },
  ];
  it("replier une ligne la cache et la résume ; la valeur reste entière", async () => {
    render(<Host f={field()} initial={two} />);
    await userEvent.click(screen.getByRole("button", { name: "Replier invité 1" }));
    const first = rows()[0] as HTMLElement;
    expect(within(first).getByRole("button", { name: "Déplier invité 1" })).toHaveAttribute("aria-expanded", "false");
    expect(within(first).getByText("Ada")).toBeVisible(); // le résumé
    expect(within(first).getByLabelText("Nom")).not.toBeVisible(); // le contenu est caché (mais reste monté : la saisie est conservée)
    expect(latest).toEqual(two); // rien n'est perdu
    await userEvent.click(screen.getByRole("button", { name: "Déplier invité 1" }));
    expect(within(rows()[0] as HTMLElement).getByLabelText("Nom")).toHaveValue("Ada");
  });
  it("tout replier, tout déplier ; l'autre ligne n'est pas touchée par une seule", async () => {
    render(<Host f={field()} initial={two} />);
    await userEvent.click(screen.getByRole("button", { name: "Replier invité 2" }));
    expect(within(rows()[0] as HTMLElement).getByLabelText("Nom")).toHaveValue("Ada");
    expect(within(rows()[1] as HTMLElement).getByLabelText("Nom")).not.toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Tout replier" }));
    expect(screen.getByRole("button", { name: "Tout replier" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Tout déplier" }));
    expect(screen.getAllByLabelText("Nom")).toHaveLength(2);
  });
  it("l'état replié suit la ligne quand on la déplace, la copie reste dépliée, supprimer décale", async () => {
    render(<Host f={field()} initial={two} />);
    await userEvent.click(screen.getByRole("button", { name: "Replier invité 1" })); // Ada repliée
    await userEvent.click(screen.getByRole("button", { name: "Descendre invité 1" }));
    expect(within(rows()[1] as HTMLElement).getByText("Ada")).toBeVisible(); // Ada est maintenant la ligne 2, toujours repliée
    expect(within(rows()[0] as HTMLElement).getByLabelText("Nom")).toHaveValue("Bob");
    await userEvent.click(screen.getByRole("button", { name: "Dupliquer invité 2" }));
    expect(within(rows()[2] as HTMLElement).getByLabelText("Nom")).toHaveValue("Ada"); // la copie est dépliée
    await userEvent.click(screen.getByRole("button", { name: "Supprimer invité 1" })); // Bob parti : Ada repliée devient la 1re
    expect(within(rows()[0] as HTMLElement).getByLabelText("Nom")).not.toBeVisible();
    expect(within(rows()[1] as HTMLElement).getByLabelText("Nom")).toHaveValue("Ada");
  });
  it("une seule ligne : pas de boutons « tout »", () => {
    render(<Host f={field()} initial={[two[0]]} />);
    expect(screen.queryByRole("button", { name: "Tout replier" })).toBeNull();
  });
});

describe("Liste de choix : replier les détails d'une option", () => {
  const items = (): FormField =>
    ({
      key: "svc",
      type: "custom",
      label: "Services",
      control: {
        ref: "svc",
        spec: {
          version: 1,
          mode: "items",
          items: [
            { value: "a", label: "Alpha" },
            { value: "b", label: "Beta" },
          ],
          parts: [{ key: "note", label: "Note", type: "text" }],
        },
      },
    }) as unknown as FormField;
  it("replier cache les détails d'une option cochée sans décocher ni perdre la saisie", async () => {
    render(<Host f={items()} initial={[{ value: "a", note: "x" }]} />);
    expect(screen.getByLabelText("Note")).toHaveValue("x");
    await userEvent.click(screen.getByRole("button", { name: "Replier les détails de Alpha" }));
    expect(screen.getByLabelText("Note")).not.toBeVisible();
    expect(screen.getByLabelText("Alpha")).toBeChecked();
    expect(latest).toEqual([{ value: "a", note: "x" }]);
    await userEvent.click(screen.getByRole("button", { name: "Déplier les détails de Alpha" }));
    expect(screen.getByLabelText("Note")).toHaveValue("x");
    expect(screen.queryByRole("button", { name: /détails de Beta/ })).toBeNull(); // pas cochée : rien à replier
  });
});
