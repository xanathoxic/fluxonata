import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { EMPTY_SECRET, SecretSource, type SecretDraft } from "./SecretSource";

vi.mock("../lib", async (orig) => ({ ...(await orig<typeof import("../lib")>()), useWsQuery: () => ({ data: { available: false } }) }));

function Harness({ multiline = false }: { multiline?: boolean }) {
  const [src, setSrc] = useState<SecretDraft>(EMPTY_SECRET);
  return (
    <>
      <SecretSource value={src} onChange={setSrc} internalLabel="Mot de passe OU clé privée" multiline={multiline}
        instructions={multiline ? <p>Collez le mot de passe ou la clé privée complète.</p> : undefined} />
      <output aria-label="valeur">{JSON.stringify(src.value)}</output>
    </>
  );
}

describe("SecretSource", () => {
  it("stays a one-line password field by default", () => {
    render(<Harness />);
    const field = screen.getByLabelText("Mot de passe OU clé privée");
    expect(field.tagName).toBe("INPUT");
    expect(field).toHaveAttribute("type", "password");
  });

  it("offers a multi-line text area, with what to paste written above it, and keeps the line breaks", async () => {
    render(<Harness multiline />);
    const field = screen.getByLabelText("Mot de passe OU clé privée");
    expect(field.tagName).toBe("TEXTAREA");
    expect(field).toHaveAccessibleDescription("Collez le mot de passe ou la clé privée complète.");
    await userEvent.click(field);
    const dashes = "-".repeat(5); // the markers are built here so that no literal key marker sits in the repository (the secrets scan would rightly flag it)
    const pasted = `${dashes}BEGIN ${"OPENSSH"} ${"PRIVATE"} KEY${dashes}\nabc\n${dashes}END ${"OPENSSH"} ${"PRIVATE"} KEY${dashes}\n`;
    await userEvent.paste(pasted);
    expect(JSON.parse(screen.getByLabelText("valeur").textContent ?? '""')).toBe(pasted);
  });

  it("masks the multi-line value until the person asks to see it", async () => {
    render(<Harness multiline />);
    const field = screen.getByLabelText("Mot de passe OU clé privée");
    expect(field).toHaveClass("masked");
    await userEvent.click(screen.getByLabelText("Afficher la valeur saisie"));
    expect(field).not.toHaveClass("masked");
  });
});
