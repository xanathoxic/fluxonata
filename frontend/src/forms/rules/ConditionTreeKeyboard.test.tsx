import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { FormField } from "../types";
import { RulesOverlay } from "./RulesOverlay";

const action: FormField = { key: "action", type: "radio", label: "Action", options: [{ value: "ajouter", label: "Ajouter" }, { value: "retirer", label: "Retirer" }] };
const autre: FormField = { key: "autre", type: "text", label: "Autre champ" };
// condition issue de l'ancienne expression « visible_if » : l'éditeur la présente comme un groupe ET d'une condition
const cible: FormField = { key: "membres", type: "checkbox_list", label: "Membres", visible_if: "values.action == 'ajouter'" };

describe("commandes segmentées de l'éditeur de règles : clavier d'un groupe radio", () => {
  it("ET / OU : la flèche change la combinaison, un seul arrêt de tabulation", async () => {
    const onApply = vi.fn();
    render(<RulesOverlay field={cible} earlier={[action, autre]} onApply={onApply} onClose={() => {}} />);
    const group = screen.getByRole("radiogroup", { name: "Combinaison" });
    const et = screen.getByRole("radio", { name: "ET" });
    const ou = screen.getByRole("radio", { name: "OU" });
    expect(et).toHaveAttribute("aria-checked", "true");
    expect([et.tabIndex, ou.tabIndex]).toEqual([0, -1]); // un seul arrêt de tabulation : le choix courant
    et.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(ou).toHaveAttribute("aria-checked", "true");
    expect(ou).toHaveFocus();
    expect([et.tabIndex, ou.tabIndex]).toEqual([-1, 0]);
    expect(group).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Appliquer" }));
    const rule = (onApply.mock.calls[0]?.[0] as FormField | undefined)?.rules?.[0];
    const when = rule?.when as { op: string } | undefined;
    expect(when?.op).toBe("any"); // le choix fait au clavier est bien celui enregistré
  });

  it("« Une valeur » / « La valeur d'un autre champ » : la flèche bascule, Début revient", async () => {
    render(<RulesOverlay field={cible} earlier={[action, autre]} onApply={() => {}} onClose={() => {}} />);
    const valeur = screen.getByRole("radio", { name: "Une valeur" });
    const champ = screen.getByRole("radio", { name: "La valeur d'un autre champ" });
    valeur.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(champ).toHaveAttribute("aria-checked", "true");
    await userEvent.keyboard("{Home}");
    expect(valeur).toHaveAttribute("aria-checked", "true");
  });
});
