import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { AppContext, type Ctx } from "../context";
import { FieldProperties } from "./FieldProperties";
import type { FormField } from "./types";

const ws = { slug: "team-a", name: "Équipe A", role: "designer", permissions: ["view", "edit"], environments: [] };
const ctx = { me: { username: "dana", is_platform_admin: false, workspaces: [ws] }, workspace: ws, environment: "test", setWorkspace: () => undefined, setEnvironment: () => undefined, can: () => true } as Ctx;

function Harness({ start }: { start: FormField }) {
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

describe("zones « une ligne par élément » des propriétés d'un champ", () => {
  it("permet d'ajouter des colonnes à un tableau : Entrée ouvre une nouvelle ligne et la saisie n'est pas reformatée", async () => {
    const u = userEvent.setup();
    render(<Harness start={{ key: "lignes", type: "datagrid", label: "Lignes", columns: [{ key: "ref", label: "Référence", type: "text" }] }} />);
    const zone = screen.getByLabelText(/Colonnes, une par ligne/) as HTMLTextAreaElement;
    await u.click(zone);
    await u.keyboard("{Control>}{End}{/Control}{Enter}");
    expect(zone.value).toBe("ref | Référence | text\n"); // la ligne vide est conservée (avant : supprimée aussitôt)
    await u.keyboard("qte | Quantité | number{Enter}pu | Prix");
    expect(zone.value).toBe("ref | Référence | text\nqte | Quantité | number\npu | Prix"); // pas de reformatage pendant la saisie
    expect(current().columns).toEqual([
      { key: "ref", label: "Référence", type: "text" },
      { key: "qte", label: "Quantité", type: "number" },
      { key: "pu", label: "Prix", type: "text" },
    ]);
  });

  it("permet aussi d'ajouter des options à une liste", async () => {
    const u = userEvent.setup();
    render(<Harness start={{ key: "choix", type: "select", label: "Choix", options: [{ value: "a", label: "Alpha" }] }} />);
    const zone = screen.getByLabelText(/Une option par ligne/) as HTMLTextAreaElement;
    await u.click(zone);
    await u.keyboard("{Control>}{End}{/Control}{Enter}b | Bravo{Enter}c");
    expect(zone.value).toBe("a | Alpha\nb | Bravo\nc");
    expect(current().options).toEqual([{ value: "a", label: "Alpha" }, { value: "b", label: "Bravo" }, { value: "c" }]);
  });

  it("garde une ligne vide au milieu pendant la saisie, et reprend le texte du champ quand on en choisit un autre", async () => {
    const u = userEvent.setup();
    const { rerender } = render(<Harness start={{ key: "t1", type: "datagrid", label: "T1", columns: [{ key: "a", type: "text" }] }} />);
    const zone = screen.getByLabelText(/Colonnes, une par ligne/) as HTMLTextAreaElement;
    await u.click(zone);
    await u.keyboard("{Control>}{End}{/Control}{Enter}{Enter}b | B");
    expect(zone.value).toBe("a |  | text\n\nb | B");
    expect((current().columns ?? []).map((c) => c.key)).toEqual(["a", "b"]); // la ligne vide ne crée pas de colonne
    // un autre champ est sélectionné : la zone affiche ses colonnes, pas le texte du précédent
    rerender(<Harness key="t2" start={{ key: "t2", type: "datagrid", label: "T2", columns: [{ key: "z", label: "Zèbre", type: "number" }] }} />);
    expect((screen.getByLabelText(/Colonnes, une par ligne/) as HTMLTextAreaElement).value).toBe("z | Zèbre | number");
  });
});
