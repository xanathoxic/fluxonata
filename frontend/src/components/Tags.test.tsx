import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { normalizeTag, TagFilter, TagInput, TagList } from "./Tags";

function Harness({ start = [] as string[] }) {
  const [v, setV] = useState(start);
  return <><TagInput value={v} onChange={setV} suggestions={["réseau", "prod"]} /><output aria-label="valeur">{v.join("|")}</output></>;
}

describe("étiquettes", () => {
  it("normalise comme le serveur", () => {
    expect(normalizeTag("  Mise à jour ")).toBe("mise-à-jour");
    expect(normalizeTag("a/b")).toBeNull();
    expect(normalizeTag("x".repeat(31))).toBeNull();
    expect(normalizeTag("-a")).toBeNull();
  });
  it("ajoute avec Entrée ou virgule, sans doublon, et retire avec ×", async () => {
    render(<Harness />);
    const input = screen.getByLabelText("Étiquettes");
    await userEvent.type(input, "Réseau{Enter}prod,{Enter}Réseau{Enter}");
    expect(screen.getByLabelText("valeur")).toHaveTextContent("réseau|prod");
    await userEvent.click(screen.getByRole("button", { name: "Retirer l'étiquette réseau" }));
    expect(screen.getByLabelText("valeur")).toHaveTextContent("prod");
  });
  it("refuse une étiquette invalide avec un message", async () => {
    render(<Harness />);
    await userEvent.type(screen.getByLabelText("Étiquettes"), "a/b{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("n'est pas une étiquette valide");
    expect(screen.getByLabelText("valeur")).toHaveTextContent("");
  });
  it("le filtre s'ouvre en liste cherchable avec les nombres, choisit et efface", async () => {
    let picked = "";
    const { rerender } = render(<TagFilter lists={[["alpha", "beta"], ["alpha"]]} value="" onChange={(t) => { picked = t; }} />);
    await userEvent.click(screen.getByRole("button", { name: /Étiquettes/ }));
    const dlg = screen.getByRole("dialog", { name: "Filtrer par étiquette" });
    expect(dlg).toHaveTextContent("alpha");
    expect(dlg).toHaveTextContent("2");
    await userEvent.type(screen.getByLabelText("Chercher une étiquette"), "bet");
    expect(screen.queryByText("alpha")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /beta/ }));
    expect(picked).toBe("beta");
    rerender(<TagFilter lists={[["alpha", "beta"], ["alpha"]]} value="beta" onChange={(t) => { picked = t; }} />);
    await userEvent.click(screen.getByRole("button", { name: "Retirer le filtre beta" }));
    expect(picked).toBe("");
    rerender(<TagList tags={["x"]} />);
    expect(screen.getByText("x")).toBeInTheDocument();
  });
});
