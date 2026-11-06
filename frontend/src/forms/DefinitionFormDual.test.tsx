import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { AppContext, type Ctx } from "../context";
import { DefinitionForm, type LookupFn } from "./DefinitionForm";
import { FieldProperties } from "./FieldProperties";
import type { FormDefinition, FormField } from "./types";

function Harness({ definition, lookup, start = {} }: { definition: FormDefinition; lookup?: LookupFn; start?: Record<string, unknown> }) {
  const [values, setValues] = useState<Record<string, unknown>>(start);
  return (<>
    <DefinitionForm definition={definition} values={values} onChange={setValues} lookup={lookup} />
    <output aria-label="valeurs">{JSON.stringify(values)}</output>
  </>);
}
const values = () => JSON.parse(screen.getByLabelText("valeurs").textContent ?? "{}") as Record<string, unknown>;
const list = (name: "Disponibles" | "Sélectionnés") => screen.getByRole("listbox", { name });
const labels = (name: "Disponibles" | "Sélectionnés") => within(list(name)).queryAllByRole("option").map((o) => o.textContent);

const OPTIONS = [{ value: "a", label: "Alpha" }, { value: "b", label: "Bravo" }, { value: "c", label: "Charlie" }];
const native = (extra: Partial<FormField> = {}): FormDefinition => ({ version: 1, sections: [{ id: "s", title: "S", fields: [{ key: "m", type: "checkbox_list", label: "Membres", display: "dual", options: OPTIONS, ...extra }] }] });

describe("liste double : champ « Cases multiples »", () => {
  it("remet au formulaire les valeurs dans l'ordre choisi (pas dans celui des options)", async () => {
    const u = userEvent.setup();
    render(<Harness definition={native()} />);
    await u.dblClick(within(list("Disponibles")).getByRole("option", { name: "Charlie" }));
    await u.dblClick(within(list("Disponibles")).getByRole("option", { name: "Alpha" }));
    expect(values().m).toEqual(["c", "a"]);
    await u.selectOptions(list("Sélectionnés"), ["a"]);
    await u.click(screen.getByRole("button", { name: "Monter" }));
    expect(values().m).toEqual(["a", "c"]);
  });

  it("affiche les titres de colonnes du champ", () => {
    render(<Harness definition={native({ available_label: "Membres du groupe", chosen_label: "À retirer" })} />);
    expect(screen.getByRole("listbox", { name: "Membres du groupe" })).toBeTruthy();
    expect(screen.getByRole("listbox", { name: "À retirer" })).toBeTruthy();
  });

  it("garde l'affichage en cases à cocher quand rien n'est demandé", () => {
    render(<Harness definition={native({ display: undefined })} />);
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.getAllByRole("checkbox")).toHaveLength(3);
  });

  it("est verrouillée par l'effet « lecture seule »", async () => {
    const u = userEvent.setup();
    const def: FormDefinition = { version: 1, sections: [{ id: "s", title: "S", fields: [
      { key: "mode", type: "select", label: "Mode", options: [{ value: "edit", label: "Modifier" }, { value: "lock", label: "Verrouiller" }] },
      { key: "m", type: "checkbox_list", label: "Membres", display: "dual", options: OPTIONS, rules: [{ when: { field: "mode", op: "equals", value: "lock" }, then: [{ do: "readonly" }], else: [{ do: "editable" }] }] },
    ] }] };
    render(<Harness definition={def} />);
    expect((list("Disponibles") as HTMLSelectElement).disabled).toBe(false);
    await u.selectOptions(screen.getByLabelText("Mode"), "lock");
    expect((list("Disponibles") as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Tout ajouter" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("lit ses options dans un service et retire les choisis de l'ancien groupe quand le groupe change (liste dépendante)", async () => {
    const u = userEvent.setup();
    const members: Record<string, string[]> = { ops: ["alice", "bruno"], dev: ["carl"] };
    const lookup = vi.fn<LookupFn>(async (key, params) => (key === "groupe" ? Object.keys(members).map((g) => ({ value: g, label: g })) : (members[params.groupe ?? ""] ?? []).map((m) => ({ value: m, label: m }))));
    const def: FormDefinition = { version: 1, sections: [{ id: "s", title: "S", fields: [
      { key: "groupe", type: "select", label: "Groupe", source: { connection: "svc", path: "/acl", items_path: "Groups", items_as: "entries" } },
      { key: "m", type: "checkbox_list", label: "Membres", display: "dual", source: { connection: "svc", path: "/acl", params: ["groupe"], items_path: "Groups.{groupe}" } },
    ] }] };
    render(<Harness definition={def} lookup={lookup} />);
    await screen.findByRole("option", { name: "ops" });
    await u.selectOptions(screen.getByLabelText("Groupe"), "ops");
    await waitFor(() => expect(labels("Disponibles")).toEqual(["alice", "bruno"]));
    await u.click(screen.getByRole("button", { name: "Tout ajouter" }));
    expect(values().m).toEqual(["alice", "bruno"]);
    await u.selectOptions(screen.getByLabelText("Groupe"), "dev");
    await waitFor(() => expect(labels("Disponibles")).toEqual(["carl"]));
    await waitFor(() => expect(values().m).toEqual([])); // plus aucun membre de « ops »
  });
});

describe("liste double : contrôle personnalisé « liste à cocher »", () => {
  const control = (extra: Record<string, unknown> = {}): FormDefinition => ({ version: 1, sections: [{ id: "s", title: "S", fields: [
    { key: "c", type: "custom", label: "Choix", control: { ref: "boites", version: "1.0.0", name: "Boîtes", spec: { version: 1, mode: "items", parts: [], items: OPTIONS, display: "dual", max: 2, ...extra } } },
  ] }] });

  it("remet les valeurs dans l'ordre choisi et respecte le maximum du contrôle", async () => {
    const u = userEvent.setup();
    render(<Harness definition={control()} />);
    await u.dblClick(within(list("Disponibles")).getByRole("option", { name: "Charlie" }));
    await u.dblClick(within(list("Disponibles")).getByRole("option", { name: "Alpha" }));
    expect(values().c).toEqual(["c", "a"]); // avec l'affichage en liste, les cases seraient rendues dans l'ordre des options
    expect((screen.getByRole("button", { name: "Tout ajouter" }) as HTMLButtonElement).disabled).toBe(true); // maximum atteint
  });

  it("affiche les titres de colonnes du contrôle", () => {
    render(<Harness definition={control({ available_label: "Équipes", chosen_label: "Retenues" })} />);
    expect(screen.getByRole("listbox", { name: "Équipes" })).toBeTruthy();
    expect(screen.getByRole("listbox", { name: "Retenues" })).toBeTruthy();
  });

  it("n'est pas utilisée quand les options ouvrent des détails (retour à la liste de cases)", () => {
    render(<Harness definition={control({ parts: [{ key: "n", type: "number", label: "Nombre" }], max: undefined })} />);
    expect(screen.queryByRole("listbox")).toBeNull();
  });
});

describe("propriétés d'un champ « Cases multiples »", () => {
  const ws = { slug: "team-a", name: "Équipe A", role: "designer", permissions: ["view", "edit"], environments: [] };
  const ctx = { me: { username: "dana", is_platform_admin: false, workspaces: [ws] }, workspace: ws, environment: "test", setWorkspace: () => undefined, setEnvironment: () => undefined, can: () => true } as Ctx;
  function Props({ start }: { start: FormField }) {
    const [field, setField] = useState<FormField>(start);
    return (
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AppContext.Provider value={ctx}>
          <FieldProperties field={field} earlier={[]} inputs={[]} connections={[]} onChange={setField} />
          <output aria-label="champ">{JSON.stringify(field)}</output>
        </AppContext.Provider>
      </QueryClientProvider>
    );
  }
  const current = () => JSON.parse(screen.getByLabelText("champ").textContent ?? "{}") as FormField;

  it("propose l'affichage en liste double, et le retire en revenant aux cases à cocher", async () => {
    const u = userEvent.setup();
    render(<Props start={{ key: "m", type: "checkbox_list", label: "Membres", options: OPTIONS }} />);
    await u.selectOptions(screen.getByLabelText("Affichage"), "dual");
    expect(current().display).toBe("dual");
    await u.selectOptions(screen.getByLabelText("Affichage"), "list");
    expect(current().display).toBeUndefined();
  });

  it("propose les titres des deux colonnes seulement en liste double, et les retire en revenant aux cases à cocher", async () => {
    const u = userEvent.setup();
    render(<Props start={{ key: "m", type: "checkbox_list", label: "Membres", options: OPTIONS }} />);
    expect(screen.queryByLabelText("Titre de la colonne de gauche")).toBeNull();
    await u.selectOptions(screen.getByLabelText("Affichage"), "dual");
    await u.type(screen.getByLabelText("Titre de la colonne de gauche"), "Membres du groupe");
    await u.type(screen.getByLabelText("Titre de la colonne de droite"), "À retirer");
    expect(current()).toMatchObject({ display: "dual", available_label: "Membres du groupe", chosen_label: "À retirer" });
    await u.clear(screen.getByLabelText("Titre de la colonne de droite"));
    expect(current().chosen_label).toBeUndefined(); // un titre vidé reprend le titre par défaut
    await u.selectOptions(screen.getByLabelText("Affichage"), "list");
    expect(current().available_label).toBeUndefined();
    expect(screen.queryByLabelText("Titre de la colonne de gauche")).toBeNull();
  });

  it("ne propose pas d'affichage pour les autres types de champ", () => {
    render(<Props start={{ key: "m", type: "select", label: "Choix", options: OPTIONS }} />);
    expect(screen.queryByLabelText("Affichage")).toBeNull();
  });
});
