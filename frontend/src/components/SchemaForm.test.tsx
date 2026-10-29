import { render, screen } from "@testing-library/react";
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
