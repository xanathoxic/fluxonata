import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { DualList, type DualOption } from "./DualList";

const OPTIONS: DualOption[] = [
  { value: "a", label: "Alpha" }, { value: "b", label: "Bravo" }, { value: "c", label: "Charlie" }, { value: "d", label: "Delta" }, { value: "e", label: "Écho" },
];

function Harness({ start = [], options = OPTIONS, max, disabled, titles }: { start?: string[]; options?: DualOption[]; max?: number; disabled?: boolean; titles?: { left?: string; right?: string } }) {
  const [value, setValue] = useState<string[]>(start);
  return (<>
    <DualList id="d" options={options} value={value} onChange={setValue} max={max} disabled={disabled} availableLabel={titles?.left} chosenLabel={titles?.right} />
    <output aria-label="valeur">{JSON.stringify(value)}</output>
  </>);
}
const value = () => JSON.parse(screen.getByLabelText("valeur").textContent ?? "[]") as string[];
const list = (name: "Disponibles" | "Sélectionnés") => screen.getByRole("listbox", { name });
const labels = (name: "Disponibles" | "Sélectionnés") => within(list(name)).queryAllByRole("option").map((o) => o.textContent);
const btn = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;

describe("DualList : déplacer", () => {
  it("affiche les disponibles à gauche et les choisis à droite, avec leur nombre", () => {
    render(<Harness start={["b"]} />);
    expect(labels("Disponibles")).toEqual(["Alpha", "Charlie", "Delta", "Écho"]);
    expect(labels("Sélectionnés")).toEqual(["Bravo"]);
    expect(screen.getByRole("heading", { name: "Disponibles (4)" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sélectionnés (1)" })).toBeTruthy();
    expect(screen.getByText(/sélectionné\(s\) sur/).textContent).toBe("1 sélectionné(s) sur 5");
  });

  it("ajoute puis retire la sélection ; l'ordre d'ajout est l'ordre de la valeur", async () => {
    const u = userEvent.setup();
    render(<Harness />);
    expect(btn("Ajouter la sélection").disabled).toBe(true); // rien de marqué
    await u.selectOptions(list("Disponibles"), ["c", "a"]);
    await u.click(btn("Ajouter la sélection"));
    expect(value()).toEqual(["a", "c"]);
    await u.selectOptions(list("Disponibles"), ["b"]);
    await u.click(btn("Ajouter la sélection"));
    expect(value()).toEqual(["a", "c", "b"]);
    await u.selectOptions(list("Sélectionnés"), ["c"]);
    await u.click(btn("Retirer la sélection"));
    expect(value()).toEqual(["a", "b"]);
    expect(labels("Disponibles")).toContain("Charlie");
  });

  it("tout ajouter / tout retirer", async () => {
    const u = userEvent.setup();
    render(<Harness />);
    await u.click(btn("Tout ajouter"));
    expect(value()).toEqual(["a", "b", "c", "d", "e"]);
    expect(btn("Tout ajouter").disabled).toBe(true);
    await u.click(btn("Tout retirer"));
    expect(value()).toEqual([]);
  });

  it("un double-clic déplace l'option, et Entrée déplace la sélection", async () => {
    const u = userEvent.setup();
    render(<Harness />);
    await u.dblClick(within(list("Disponibles")).getByRole("option", { name: "Delta" }));
    expect(value()).toEqual(["d"]);
    await u.selectOptions(list("Disponibles"), ["a", "b"]);
    await u.type(list("Disponibles"), "{Enter}", { skipClick: true });
    expect(value()).toEqual(["d", "a", "b"]);
    await u.dblClick(within(list("Sélectionnés")).getByRole("option", { name: "Alpha" }));
    expect(value()).toEqual(["d", "b"]);
  });
});

describe("DualList : recherche, maximum, options grisées", () => {
  it("la recherche filtre sans tenir compte des accents ni de la casse, et « tout ajouter » n'ajoute que ce qui est affiché", async () => {
    const u = userEvent.setup();
    render(<Harness />);
    await u.type(screen.getByLabelText("Rechercher dans « Disponibles »"), "ECHO");
    expect(labels("Disponibles")).toEqual(["Écho"]);
    await u.click(btn("Tout ajouter"));
    expect(value()).toEqual(["e"]);
  });

  it("respecte le maximum : rien de plus n'est ajouté et le nombre autorisé est indiqué", async () => {
    const u = userEvent.setup();
    render(<Harness max={2} />);
    expect(screen.getByText(/sélectionné\(s\) sur/).textContent).toContain("au plus 2");
    await u.click(btn("Tout ajouter"));
    expect(value()).toEqual(["a", "b"]);
    expect(btn("Tout ajouter").disabled).toBe(true);
    await u.selectOptions(list("Disponibles"), ["c"]);
    expect(btn("Ajouter la sélection").disabled).toBe(true);
  });

  it("n'ajoute jamais une option grisée, même avec « tout ajouter »", async () => {
    const u = userEvent.setup();
    render(<Harness options={[{ value: "a", label: "Alpha" }, { value: "x", label: "Interdit", disabled: true }, { value: "b", label: "Bravo" }]} />);
    expect((within(list("Disponibles")).getByRole("option", { name: "Interdit" }) as HTMLOptionElement).disabled).toBe(true);
    await u.click(btn("Tout ajouter"));
    expect(value()).toEqual(["a", "b"]);
  });

  it("range les options par groupe", () => {
    render(<Harness options={[{ value: "a", label: "Alpha", group: "G1" }, { value: "b", label: "Bravo", group: "G1" }, { value: "c", label: "Charlie", group: "G2" }]} />);
    expect(within(list("Disponibles")).getAllByRole("group").map((g) => g.getAttribute("label"))).toEqual(["G1", "G2"]);
  });

  it("garde une valeur choisie que les options ne connaissent pas (encore) au lieu de la perdre", async () => {
    const u = userEvent.setup();
    render(<Harness start={["inconnu"]} />);
    expect(labels("Sélectionnés")).toEqual(["inconnu"]);
    await u.dblClick(within(list("Disponibles")).getByRole("option", { name: "Alpha" }));
    expect(value()).toEqual(["inconnu", "a"]);
  });
});

describe("DualList : mettre en ordre", () => {
  it("monte, descend, envoie tout en haut / tout en bas (la sélection garde son ordre)", async () => {
    const u = userEvent.setup();
    render(<Harness start={["a", "b", "c", "d"]} />);
    await u.selectOptions(list("Sélectionnés"), ["c"]);
    await u.click(btn("Monter"));
    expect(value()).toEqual(["a", "c", "b", "d"]);
    await u.click(btn("Descendre"));
    await u.click(btn("Descendre"));
    expect(value()).toEqual(["a", "b", "d", "c"]);
    await u.click(btn("Tout en haut"));
    expect(value()).toEqual(["c", "a", "b", "d"]);
    await u.click(btn("Tout en bas"));
    expect(value()).toEqual(["a", "b", "d", "c"]);
    await u.deselectOptions(list("Sélectionnés"), ["c"]);
    await u.selectOptions(list("Sélectionnés"), ["a", "d"]); // plusieurs : leur ordre relatif est conservé
    await u.click(btn("Tout en bas"));
    expect(value()).toEqual(["b", "c", "a", "d"]);
  });

  it("n'envoie rien de plus haut que le début", async () => {
    const u = userEvent.setup();
    render(<Harness start={["a", "b"]} />);
    await u.selectOptions(list("Sélectionnés"), ["a"]);
    await u.click(btn("Monter"));
    expect(value()).toEqual(["a", "b"]);
  });

  it("Alt + flèche met en ordre au clavier", async () => {
    const u = userEvent.setup();
    render(<Harness start={["a", "b", "c"]} />);
    await u.selectOptions(list("Sélectionnés"), ["b"]);
    await u.keyboard("{Alt>}{ArrowUp}{/Alt}");
    expect(value()).toEqual(["b", "a", "c"]);
  });

  it("refuse de mettre en ordre pendant une recherche (l'ordre des éléments affichés serait ambigu) et le dit", async () => {
    const u = userEvent.setup();
    render(<Harness start={["a", "b", "c"]} />);
    await u.selectOptions(list("Sélectionnés"), ["b"]);
    expect(btn("Monter").disabled).toBe(false);
    await u.type(screen.getByLabelText("Rechercher dans « Sélectionnés »"), "b");
    expect(btn("Monter").disabled).toBe(true);
    expect(screen.getByText(/Videz la recherche pour remettre en ordre/)).toBeTruthy();
  });
});

describe("DualList : lecture seule", () => {
  it("désactive tout (listes, recherches, boutons)", () => {
    render(<Harness start={["a"]} disabled />);
    expect((list("Disponibles") as HTMLSelectElement).disabled).toBe(true);
    expect((list("Sélectionnés") as HTMLSelectElement).disabled).toBe(true);
    for (const name of ["Tout ajouter", "Tout retirer", "Monter", "Descendre"]) expect(btn(name).disabled).toBe(true);
    expect((screen.getByLabelText("Rechercher dans « Disponibles »") as HTMLInputElement).disabled).toBe(true);
  });
});

describe("DualList : titres des colonnes", () => {
  it("affiche les titres choisis, pour les en-têtes, les listes et les recherches", () => {
    render(<Harness start={["a"]} titles={{ left: "Membres du groupe", right: "À retirer" }} />);
    expect(screen.getByRole("heading", { name: "Membres du groupe (4)" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "À retirer (1)" })).toBeTruthy();
    expect(screen.getByRole("listbox", { name: "Membres du groupe" })).toBeTruthy();
    expect(screen.getByRole("listbox", { name: "À retirer" })).toBeTruthy();
    expect(screen.getByLabelText("Rechercher dans « Membres du groupe »")).toBeTruthy();
    expect(screen.getByLabelText("Rechercher dans « À retirer »")).toBeTruthy();
    expect(screen.queryByRole("listbox", { name: "Disponibles" })).toBeNull();
  });

  it("revient aux titres par défaut quand un titre est vide ou absent, et ne change qu'une colonne si on n'en renomme qu'une", () => {
    render(<Harness titles={{ left: "   ", right: "Choisis" }} />);
    expect(screen.getByRole("listbox", { name: "Disponibles" })).toBeTruthy();
    expect(screen.getByRole("listbox", { name: "Choisis" })).toBeTruthy();
  });
});

