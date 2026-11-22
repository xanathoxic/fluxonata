import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { DefinitionForm } from "./DefinitionForm";
import { submitView, validateDefinition } from "./logic";
import { RulesOverlay } from "./rules/RulesOverlay";
import { SUBMIT_FIELD, type Rule } from "./rules";
import type { FormDefinition, FormField } from "./types";

/** Une règle écrite en JSON : « then » est le nom du format des règles (contrat d'API), pas une promesse. */
const rule = (json: string): Rule => JSON.parse(json);
const BLOCKED = "Cochez la confirmation.";
const ENABLE = rule(
  `{"when":{"field":"ok","op":"is_true"},"then":[{"do":"editable"}],"else":[{"do":"readonly"},{"do":"text","target":"help","text":"${BLOCKED}"}]}`,
);
const fields: FormField[] = [
  { key: "nom", type: "text", label: "Nom" },
  { key: "ok", type: "checkbox", label: "J'ai lu" },
];
const withSubmit = (...rules: Rule[]): FormDefinition => ({ version: 1, sections: [{ id: "s", title: "S", fields }], submit: { rules } });

function Harness({ definition }: { definition: FormDefinition }) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  return (
    <DefinitionForm definition={definition} values={values} onChange={setValues}>
      {(s) => (
        <button type="submit" disabled={s.blocked}>
          {s.label ?? "Envoyer"}
        </button>
      )}
    </DefinitionForm>
  );
}

describe("conditions du bouton d'envoi", () => {
  it("sans condition, le bouton est affiché et actif", () => {
    expect(submitView({ version: 1, sections: [{ title: "S", fields }] }, {})).toEqual({ visible: true, blocked: false, label: null, message: null });
    expect(submitView({ version: 1, sections: [{ title: "S", fields }], submit: { rules: [] } }, {}).blocked).toBe(false);
  });

  it("bloque le bouton avec un message tant que la case n'est pas cochée, puis le débloque", async () => {
    const u = userEvent.setup();
    render(<Harness definition={withSubmit(ENABLE)} />);
    const button = screen.getByRole("button", { name: "Envoyer" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toBe(BLOCKED);
    await u.click(screen.getByLabelText("J'ai lu"));
    expect(button.disabled).toBe(false);
    expect(screen.queryByText(BLOCKED)).toBeNull();
  });

  it("masque le bouton quand une condition le demande", async () => {
    const u = userEvent.setup();
    render(<Harness definition={withSubmit(rule(`{"when":{"field":"ok","op":"is_true"},"then":[{"do":"show"}],"else":[{"do":"hide"}]}`))} />);
    expect(screen.queryByRole("button", { name: "Envoyer" })).toBeNull();
    await u.click(screen.getByLabelText("J'ai lu"));
    expect(screen.getByRole("button", { name: "Envoyer" })).toBeTruthy();
  });

  it("change le libellé du bouton", async () => {
    const u = userEvent.setup();
    render(
      <Harness
        definition={withSubmit(rule(`{"when":{"field":"ok","op":"is_true"},"then":[{"do":"text","target":"label","text":"Demander l'approbation de {nom}"}]}`))}
      />,
    );
    expect(screen.getByRole("button", { name: "Envoyer" })).toBeTruthy();
    await u.type(screen.getByLabelText("Nom"), "Alice");
    await u.click(screen.getByLabelText("J'ai lu"));
    expect(screen.getByRole("button", { name: "Demander l'approbation de Alice" })).toBeTruthy();
  });

  it("désactive aussi des boutons passés tels quels (contenu simple)", () => {
    render(
      <DefinitionForm definition={withSubmit(ENABLE)} values={{}} onChange={() => {}}>
        <button type="submit">Lancer</button>
      </DefinitionForm>,
    );
    expect((screen.getByRole("button", { name: "Lancer" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("la validation refuse l'envoi comme le serveur (message, ou un message par défaut)", () => {
    expect(validateDefinition(withSubmit(ENABLE), { ok: false })).toEqual({ _: BLOCKED });
    expect(validateDefinition(withSubmit(ENABLE), { ok: true })).toEqual({});
    expect(validateDefinition(withSubmit(rule('{"when":{"field":"ok","op":"is_false"},"then":[{"do":"hide"}]}')), { ok: false })._).toMatch(/condition/);
  });
});

describe("fenêtre des conditions du bouton d'envoi", () => {
  it("propose les modèles et les effets d'un bouton, pas ceux d'un champ, et renvoie les règles", async () => {
    const u = userEvent.setup();
    let applied: FormField | null = null;
    render(
      <RulesOverlay
        field={SUBMIT_FIELD}
        earlier={fields}
        onApply={(f) => {
          applied = f;
        }}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("Conditions du bouton d'envoi")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Activer le bouton seulement si/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Rendre obligatoire/ })).toBeNull();
    await u.click(screen.getByRole("button", { name: /Activer le bouton seulement si/ }));
    expect(screen.getAllByRole("button", { name: "Ajouter : Bloquer le bouton" }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /Ajouter : Rendre obligatoire/ })).toBeNull();
    await u.click(screen.getByRole("button", { name: "Appliquer" }));
    const rules = (applied as FormField | null)?.rules ?? [];
    expect(rules).toHaveLength(1);
    expect(rules[0]?.then).toEqual([{ do: "editable" }]);
  });
});
