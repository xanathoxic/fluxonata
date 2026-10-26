import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { JsonSchema } from "../api";
import { WizardForm } from "./WizardForm";
import type { FormDef } from "./types";

const schema: JsonSchema = { type: "object", required: ["a", "c"], properties: { a: { type: "string", title: "Alpha" }, b: { type: "string", title: "Bravo" }, c: { type: "string", title: "Charlie" } } };
const form: FormDef = { steps: [{ title: "Première", fields: ["a", "b"] }, { title: "Seconde", fields: ["c"] }] };

function Harness({ onSubmit }: { onSubmit: () => void }) {
  const [v, setV] = useState<Record<string, unknown>>({});
  return <WizardForm schema={schema} form={form} values={v} onChange={setV} submitLabel="Lancer" onSubmit={onSubmit} />;
}

describe("WizardForm (step-by-step start dialog)", () => {
  it("walks through steps, blocks on required fields, marks finished steps and submits at the end", async () => {
    const submit = vi.fn();
    render(<Harness onSubmit={submit} />);
    expect(screen.getByRole("navigation", { name: "Étapes du formulaire" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Suivant" }));
    expect(screen.getByRole("alert")).toHaveTextContent("obligatoire"); // Alpha is required: stays on step 1
    expect(screen.getByRole("heading", { name: /1\. Première/ })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/Alpha/), "x");
    await userEvent.click(screen.getByRole("button", { name: "Suivant" }));
    expect(screen.getByRole("heading", { name: /2\. Seconde/ })).toBeInTheDocument();
    expect(screen.getByText("(terminée)")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Alpha/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Lancer" }));
    expect(submit).not.toHaveBeenCalled(); // Charlie missing
    await userEvent.type(screen.getByLabelText(/Charlie/), "y");
    await userEvent.click(screen.getByRole("button", { name: "Lancer" }));
    expect(submit).toHaveBeenCalledTimes(1);
  });

  it("without a form definition shows a single step with every input", () => {
    render(<WizardForm schema={schema} values={{}} onChange={() => {}} submitLabel="Lancer" />);
    expect(screen.queryByRole("navigation", { name: "Étapes du formulaire" })).not.toBeInTheDocument();
    expect(screen.getByLabelText(/Alpha/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Charlie/)).toBeInTheDocument();
  });
});
