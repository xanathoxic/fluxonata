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
