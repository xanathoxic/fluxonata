import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { SecretChoice, emptyChoice, secretChoicePayload, secretChoiceReady, type SecretChoiceDraft, type SecretRow } from "./SecretChoice";

vi.mock("../lib", async (orig) => ({ ...(await orig<typeof import("../lib")>()), useWsQuery: () => ({ data: { available: false } }) }));

const SECRETS: SecretRow[] = [{ name: "partage", provider: "internal", used_by: 2 }, { name: "ext", provider: "vault", used_by: 0 }];

function Harness({ start, current, secrets = SECRETS }: { start: SecretChoiceDraft; current?: string | null; secrets?: SecretRow[] }) {
  const [c, setC] = useState(start);
  return (
    <>
      <SecretChoice value={c} onChange={setC} secrets={secrets} {...(current !== undefined ? { current } : {})} />
      <output aria-label="payload">{JSON.stringify(secretChoicePayload(c))}</output>
      <output aria-label="prêt">{String(secretChoiceReady(c))}</output>
    </>
  );
}
const payload = () => JSON.parse(screen.getByLabelText("payload").textContent ?? "{}") as Record<string, unknown>;

describe("SecretChoice", () => {
  it("offers to keep the current secret only when editing, and keeping sends nothing", () => {
    render(<Harness start={emptyChoice("keep")} current="base-all" />);
    expect(screen.getByLabelText(/Conserver le secret actuel \(base-all\)/)).toBeChecked();
    expect(payload()).toEqual({});
    expect(screen.getByLabelText("prêt")).toHaveTextContent("true");
  });

  it("has no « keep » choice on creation", () => {
    render(<Harness start={emptyChoice("new")} />);
    expect(screen.queryByLabelText(/Conserver le secret actuel/)).toBeNull();
  });

  it("picks an existing secret by name, showing provider and usage but never a value", async () => {
    render(<Harness start={emptyChoice("keep")} current="x" />);
    await userEvent.click(screen.getByLabelText(/Choisir un secret existant/));
    expect(screen.getByLabelText("prêt")).toHaveTextContent("false"); // nothing chosen yet
    const select = screen.getByLabelText("Secret existant");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Choisir…", "partage — coffre de Fluxonata, 2 connexion(s)", "ext — Vault, 0 connexion(s)"]);
    await userEvent.selectOptions(select, "partage");
    expect(payload()).toEqual({ secret: "partage" });
    expect(screen.getByLabelText("prêt")).toHaveTextContent("true");
  });

  it("disables the existing-secret choice when the workspace has no secret", () => {
    render(<Harness start={emptyChoice("new")} secrets={[]} />);
    expect(screen.getByLabelText(/Choisir un secret existant/)).toBeDisabled();
  });

  it("creates a new secret, optionally named, and is ready only once a value is typed", async () => {
    render(<Harness start={emptyChoice("keep")} current="x" />);
    await userEvent.click(screen.getByLabelText(/Créer un nouveau secret/));
    expect(screen.getByLabelText("prêt")).toHaveTextContent("false");
    await userEvent.type(screen.getByLabelText("Nom du nouveau secret (facultatif)"), "mon-secret");
    await userEvent.type(screen.getByLabelText(/Secret \(jeton, mot de passe…\)/), "valeur-synthetique");
    expect(payload()).toEqual({ secret_provider: "internal", secret_value: "valeur-synthetique", new_secret_name: "mon-secret" });
    expect(screen.getByLabelText("prêt")).toHaveTextContent("true");
  });

  it("never sends the previous mode's data after a change of mind", async () => {
    render(<Harness start={emptyChoice("new")} />);
    await userEvent.type(screen.getByLabelText(/Secret \(jeton, mot de passe…\)/), "valeur-synthetique");
    await userEvent.click(screen.getByLabelText(/Choisir un secret existant/));
    await userEvent.selectOptions(screen.getByLabelText("Secret existant"), "partage");
    expect(payload()).toEqual({ secret: "partage" }); // the typed value is not sent along
  });
});
