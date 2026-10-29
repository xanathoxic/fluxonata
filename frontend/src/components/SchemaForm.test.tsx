import { EditorView } from "@codemirror/view";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { defaultsFor, SchemaForm, validateValues } from "./SchemaForm";
import type { JsonSchema } from "../api";

const schema: JsonSchema = {
  type: "object",
  required: ["text"],
  properties: {
    text: { type: "string", title: "Texte", description: "Le texte.", minLength: 2 },
    shout: { type: "boolean", title: "Majuscules", default: false },
    n: { type: "integer", title: "Nombre", minimum: 1, maximum: 5, default: 3 },
  },
};

function Harness({ errors }: { errors?: Record<string, string> }) {
  const [values, setValues] = useState(defaultsFor(schema));
  return <SchemaForm schema={schema} values={values} onChange={setValues} errors={errors} />;
}

describe("SchemaForm", () => {
  it("renders labelled controls with defaults and required marker", () => {
    render(<Harness />);
    expect(screen.getByLabelText(/Texte/)).toBeRequired();
    expect(screen.getByLabelText("Majuscules")).not.toBeChecked();
    expect(screen.getByLabelText("Nombre")).toHaveValue(3);
    expect(screen.getByLabelText(/Texte/)).toHaveAccessibleDescription("Le texte.");
  });

  it("associates the error with its field", () => {
    render(<Harness errors={{ text: "Ce champ est obligatoire." }} />);
    const field = screen.getByLabelText(/Texte/);
    expect(field).toBeInvalid();
    expect(field).toHaveAccessibleDescription(/Ce champ est obligatoire/);
    expect(screen.getByRole("alert")).toHaveTextContent("Erreur");
  });

  it("is usable with the keyboard only", async () => {
    render(<Harness />);
    await userEvent.tab();
    await userEvent.keyboard("hello");
    expect(screen.getByLabelText(/Texte/)).toHaveValue("hello");
    await userEvent.tab();
    await userEvent.keyboard(" ");
    expect(screen.getByLabelText("Majuscules")).toBeChecked();
  });
});

describe("validateValues", () => {
  it("flags missing required, short strings and out-of-range numbers", () => {
    expect(validateValues(schema, {})).toEqual({ text: "Ce champ est obligatoire." });
    expect(validateValues(schema, { text: "a" }).text).toMatch(/Au moins 2/);
    expect(validateValues(schema, { text: "ok", n: 9 }).n).toMatch(/Maximum/);
    expect(validateValues(schema, { text: "ok", n: 2.5 }).n).toMatch(/entier/);
    expect(validateValues(schema, { text: "ok", n: 2 })).toEqual({});
  });
});

const structured: JsonSchema = {
  type: "object", required: ["data", "fields"],
  properties: { data: { type: "object", title: "Objet source" }, fields: { type: "array", title: "Champs à conserver" } },
};

let latest: Record<string, unknown> = {};
function JsonHarness({ initial }: { initial: Record<string, unknown> }) {
  const [v, setV] = useState(initial);
  latest = v;
  return <SchemaForm schema={structured} values={v} onChange={setV} />;
}

function typeInto(container: HTMLElement, index: number, text: string) {
  const view = EditorView.findFromDOM(container.querySelectorAll<HTMLElement>(".cm-editor")[index] as HTMLElement);
  if (!view) throw new Error("éditeur introuvable");
  act(() => view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } }));
}

describe("SchemaForm : objets et listes", () => {
  it("affiche le JSON dans un éditeur (pas « [object Object] ») et renvoie des valeurs structurées", () => {
    const { container } = render(<JsonHarness initial={{ data: { a: 1 }, fields: ["a"] }} />);
    expect(screen.getByLabelText(/Objet source \(JSON objet\)/)).toHaveTextContent('"a": 1');
    expect(container).not.toHaveTextContent("[object Object]");
    typeInto(container, 0, '{"x": 10, "y": [1, 2]}');
    expect(latest.data).toEqual({ x: 10, y: [1, 2] });
    typeInto(container, 1, '["x", "y"]');
    expect(latest.fields).toEqual(["x", "y"]);
  });

  it("garde le texte tant que le JSON est invalide, le signale, puis se met en forme", async () => {
    const { container } = render(<JsonHarness initial={{ data: {}, fields: [] }} />);
    typeInto(container, 0, "{ pas du json");
    expect(latest.data).toBe("{ pas du json");
    expect(validateValues(structured, latest).data).toMatch(/^JSON invalide/);
    expect(screen.getByText(/^JSON invalide/)).toBeInTheDocument();
    typeInto(container, 0, '{"b":2}');
    expect(latest.data).toEqual({ b: 2 });
    await userEvent.click(screen.getAllByRole("button", { name: "Mettre en forme" })[0] as HTMLElement);
    expect(screen.getByLabelText(/Objet source/)).toHaveTextContent('"b": 2');
  });
});
