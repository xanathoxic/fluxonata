import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PickPanel } from "./PickPanel";
import { ancestorsOf, candidatesFor, describeElement, kindName, snapTarget } from "./pick";
import { selectorsFor } from "./targets";

const HTML = `<form class="fb-form"><fieldset class="fb-section"><legend>Infos</legend>
  <div class="fb-cell" data-key="nom" data-type="text"><div class="fb-field"><label>Nom</label><input type="text"/></div></div>
  <div class="fb-cell" data-key="adr" data-type="custom"><div class="fb-field"><span class="fb-label">Adresse</span>
    <div class="ctl"><div class="ctl-row"><div class="ctl-rowhead"><button class="ctl-fold"><svg><path/></svg></button><strong>Ligne 1</strong></div>
      <div class="fb-cell" data-key="ville" data-type="text"><div class="fb-field"><label>Ville</label><input type="text"/></div></div></div>
      <div class="ctl-row"><div class="ctl-rowhead"><strong>Ligne 2</strong></div></div></div></div></div>
  <div class="fb-actions"><button>Envoyer</button></div></fieldset></form>`;
const dom = () => {
  const d = document.createElement("div");
  d.innerHTML = HTML;
  document.body.append(d);
  return { d, root: d.querySelector(".fb-form") as Element };
};
const q = (d: Element, s: string) => d.querySelector(s) as Element;

describe("Cible libre : viser sans écrire de chemin", () => {
  it("survoler une icône vise le bouton qui la porte ; le formulaire entier n'est jamais une cible", () => {
    const { d, root } = dom();
    expect(snapTarget(q(d, "svg path"), root)).toBe(q(d, ".ctl-fold"));
    expect(snapTarget(root, root)).toBeNull();
  });
  it("nomme l'élément en clair, avec son champ", () => {
    const { d, root } = dom();
    expect(describeElement(q(d, ".fb-cell[data-key=nom] label"), root)).toBe("Libellé · champ « Nom »");
    expect(describeElement(q(d, ".ctl-rowhead"), root)).toBe("En-tête d'une ligne · champ « Adresse »");
    expect(describeElement(q(d, ".fb-actions button"), root)).toBe("Bouton");
  });
  it("« cet élément précis » ne désigne qu'un élément, même parmi des lignes semblables", () => {
    const { d, root } = dom();
    const second = candidatesFor(q(d, ".ctl-row:nth-child(2) .ctl-rowhead"), root).find((c) => c.id === "one");
    expect(second?.count).toBe(1);
    expect(root.querySelector(`:scope ${second?.selector}`)).toBe(q(d, ".ctl-row:nth-child(2) .ctl-rowhead"));
    const label = candidatesFor(q(d, "[data-key=nom] label"), root).find((c) => c.id === "one");
    expect(label?.selector).toBe('[data-key="nom"] > .fb-field > label');
  });
  it("propose aussi « tous ceux de ce genre » et « ceux de ce champ », avec leur nombre", () => {
    const { d, root } = dom();
    const list = candidatesFor(q(d, ".ctl-rowhead"), root);
    expect(list.map((c) => c.id)).toEqual(["one", "kind", "inField", "cell"]);
    expect(list.find((c) => c.id === "kind")).toMatchObject({ selector: ".ctl-row > .ctl-rowhead", count: 2 });
    expect(list.find((c) => c.id === "inField")).toMatchObject({ selector: '[data-key="adr"] .ctl-rowhead', count: 2 });
  });
  it("cliquer la case d'un champ propose toute la case ; sans champ autour (boutons), pas d'option « ce champ »", () => {
    const { d, root } = dom();
    expect(candidatesFor(q(d, ".fb-cell[data-key=nom]"), root).map((c) => c.selector)).toEqual(['[data-key="nom"]']);
    const buttons = candidatesFor(q(d, ".fb-actions button"), root).map((c) => c.id);
    expect(buttons).toContain("one");
    expect(buttons).not.toContain("inField");
  });
  it("les sélecteurs écrits sont ceux que le concepteur sait préfixer (« & »)", () => {
    const { d, root } = dom();
    for (const c of candidatesFor(q(d, ".ctl-fold"), root))
      expect(selectorsFor({ scope: { kind: "all" }, part: "custom", state: "normal", screen: "all", custom: c.selector })[0]?.startsWith("& ")).toBe(true);
  });
});

describe("Viser le parent", () => {
  it("liste l'élément et ses parents qui se laissent viser, du plus proche au plus lointain, sans le formulaire", () => {
    const { d, root } = dom();
    const chain = ancestorsOf(q(d, "[data-key=nom] label"), root);
    expect(chain.map(kindName)).toEqual(["libellé", "champ (libellé + saisie)", "case du champ", "section"]);
    expect(chain[0]).toBe(q(d, "[data-key=nom] label"));
    expect(chain).not.toContain(root);
  });
  it("une icône donne le bouton, puis ses parents jusqu'au contrôle", () => {
    const { d, root } = dom();
    expect(ancestorsOf(q(d, "svg path"), root).map(kindName).slice(0, 4)).toEqual([
      "bouton replier / déplier",
      "en-tête d'une ligne",
      "ligne",
      "contrôle personnalisé",
    ]);
  });
  it("viser un parent donne ses propres choix, avec leur nombre", () => {
    const { d, root } = dom();
    const [label, field, cell] = ancestorsOf(q(d, "[data-key=nom] label"), root) as [Element, Element, Element];
    expect(candidatesFor(label, root).find((c) => c.id === "one")?.selector).toBe('[data-key="nom"] > .fb-field > label');
    expect(candidatesFor(field, root).find((c) => c.id === "one")?.selector).toBe('[data-key="nom"] > .fb-field');
    const parent = candidatesFor(cell, root);
    expect(parent.map((c) => c.selector)).toEqual(['[data-key="nom"]']); // la case du champ
    expect(parent[0]?.count).toBe(1);
  });
  it("le parent d'un élément de premier niveau s'arrête à la section", () => {
    const { d, root } = dom();
    expect(ancestorsOf(q(d, ".fb-actions button"), root).map(kindName)).toEqual(["bouton", "zone des boutons", "section"]);
  });
});

describe("PickPanel", () => {
  const cands = [
    { id: "one", label: "Cet élément précis", selector: "a", count: 1 },
    { id: "kind", label: "Tous les éléments de ce genre", selector: "b", count: 12 },
  ];
  it("montre l'élément survolé, les choix avec leur nombre, et choisit au clic", async () => {
    const choose = vi.fn();
    render(<PickPanel hover="Libellé · champ « Nom »" candidates={cands} selector="a" onChoose={choose} onSelector={() => undefined} />);
    expect(screen.getByText(/Libellé · champ « Nom »/)).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Cet élément précis/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("12 éléments")).toBeTruthy();
    expect(screen.getByText("1 élément")).toBeTruthy();
    await userEvent.click(screen.getByRole("radio", { name: /Tous les éléments de ce genre/ }));
    expect(choose).toHaveBeenCalledWith("b");
  });
  it("rien survolé ni cliqué : une consigne, pas de choix ; le chemin reste modifiable en avancé", async () => {
    const onSelector = vi.fn();
    render(<PickPanel hover="" candidates={[]} selector="" onChoose={() => undefined} onSelector={onSelector} />);
    expect(screen.getByText("Rien sous la souris")).toBeTruthy();
    expect(screen.queryByRole("radiogroup")).toBeNull();
    await userEvent.click(screen.getByText(/Chemin de la cible/));
    await userEvent.type(screen.getByLabelText("Chemin de la cible"), "p");
    expect(onSelector).toHaveBeenCalledWith("p");
  });
});

describe("PickPanel : niveaux", () => {
  const levels = [
    { label: "case du champ", title: "a" },
    { label: "champ", title: "b" },
    { label: "libellé", title: "c" },
  ];
  it("montre le chemin, le niveau visé ; Parent et Enfant changent de niveau", async () => {
    const onLevel = vi.fn();
    render(
      <PickPanel hover="" candidates={[]} selector="" levels={levels} level={2} onLevel={onLevel} onChoose={() => undefined} onSelector={() => undefined} />,
    );
    expect(screen.getByRole("button", { name: "libellé" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: /Enfant/ })).toBeDisabled(); // déjà l'élément cliqué
    await userEvent.click(screen.getByRole("button", { name: /Parent/ }));
    expect(onLevel).toHaveBeenCalledWith(1);
    await userEvent.click(screen.getByRole("button", { name: "case du champ" }));
    expect(onLevel).toHaveBeenLastCalledWith(0);
  });
  it("tout en haut : plus de parent ; sans niveaux (rien cliqué) : pas de ligne de niveaux", () => {
    const { rerender } = render(
      <PickPanel
        hover=""
        candidates={[]}
        selector=""
        levels={levels}
        level={0}
        onLevel={() => undefined}
        onChoose={() => undefined}
        onSelector={() => undefined}
      />,
    );
    expect(screen.getByRole("button", { name: /Parent/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Enfant/ })).toBeEnabled();
    rerender(<PickPanel hover="" candidates={[]} selector="" onChoose={() => undefined} onSelector={() => undefined} />);
    expect(screen.queryByRole("navigation")).toBeNull();
  });
});
