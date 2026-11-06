import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { FormField } from "../types";
import { RulesOverlay } from "./RulesOverlay";

const action: FormField = { key: "action", type: "radio", label: "Action", options: [{ value: "ajouter", label: "Ajouter" }, { value: "retirer", label: "Retirer" }] };
// la forme SIMPLE d'une condition : un seul {champ, opérateur, valeur}, sans groupe (ce que le serveur et le MCP acceptent)
const simple: FormField = { key: "membres", type: "checkbox_list", label: "Membres", rules: [{ name: "Pas pour un ajout", when: { field: "action", op: "equals", value: "ajouter" }, then: [{ do: "hide" }], else: [{ do: "show" }] }] };

describe("RulesOverlay", () => {
  it("ouvre l'éditeur du « Quand » pour une règle écrite avec une condition simple (sans groupe)", async () => {
    const u = userEvent.setup();
    const onApply = vi.fn();
    render(<RulesOverlay field={simple} earlier={[action]} onApply={onApply} onClose={() => {}} />);
    // la condition est éditable : son champ, son opérateur et sa valeur sont affichés
    expect((screen.getByLabelText("Condition 1 : champ") as HTMLSelectElement).value).toBe("action");
    expect((screen.getByLabelText("Condition 1 : opérateur") as HTMLSelectElement).value).toBe("equals");
    expect((screen.getByLabelText("Condition 1 : valeur") as HTMLSelectElement).value).toBe("ajouter");
    // et la modifier fonctionne (la règle devient un groupe d'une condition)
    await u.selectOptions(screen.getByLabelText("Condition 1 : valeur"), "retirer");
    await u.click(screen.getByRole("button", { name: "Appliquer" }));
    const rule = (onApply.mock.calls[0]?.[0] as FormField).rules?.[0];
    expect(rule?.when).toEqual({ op: "all", items: [{ field: "action", op: "equals", value: "retirer" }] });
  });

  it("propose « Lecture seule si… » et les effets « Mettre en lecture seule » / « Rendre modifiable »", async () => {
    const u = userEvent.setup();
    const onApply = vi.fn();
    render(<RulesOverlay field={{ key: "x", type: "text", label: "X" }} earlier={[action]} onApply={onApply} onClose={() => {}} />);
    await u.click(screen.getByRole("button", { name: /Lecture seule si/ }));
    expect(screen.getAllByRole("button", { name: "Ajouter : Mettre en lecture seule" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "Ajouter : Rendre modifiable" }).length).toBeGreaterThan(0);
    await u.click(screen.getByRole("button", { name: "Appliquer" }));
    const rule = (onApply.mock.calls[0]?.[0] as FormField).rules?.[0];
    expect(rule?.then).toEqual([{ do: "readonly" }]);
    expect(rule?.else).toEqual([{ do: "editable" }]);
  });

  it("ne propose pas la lecture seule pour un bloc de contenu (il n'a pas de valeur)", () => {
    render(<RulesOverlay field={{ key: "b", type: "paragraph", label: "Texte" }} earlier={[action]} onApply={() => {}} onClose={() => {}} />);
    expect(screen.queryByRole("button", { name: "Ajouter : Mettre en lecture seule" })).toBeNull();
  });
});
