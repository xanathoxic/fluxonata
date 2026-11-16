import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ReadRegion } from "./ReadRegion";

describe("zone de lecture défilante", () => {
  it("expose un nom et un rôle de région et reste atteignable au clavier", async () => {
    render(
      <>
        <button type="button">avant</button>
        <ReadRegion label="Journal de l'étape" className="log">
          ligne 1{"\n"}ligne 2
        </ReadRegion>
      </>,
    );
    const region = screen.getByRole("region", { name: "Journal de l'étape" });
    expect(region.tagName).toBe("PRE"); // la mise en forme du texte est conservée
    expect(region).toHaveAttribute("tabindex", "0");
    await userEvent.tab(); // bouton « avant »
    await userEvent.tab(); // la zone
    expect(region).toHaveFocus();
    expect(region).toHaveClass("log");
  });

  it("garde le rôle journal pour un journal et utilise la section native sans ajouter de rôle", () => {
    render(
      <>
        <ReadRegion label="Journal du test" role="log">
          en cours
        </ReadRegion>
        <ReadRegion label="Différences" as="section">
          diff
        </ReadRegion>
        <ReadRegion label="Table large" as="div">
          table
        </ReadRegion>
      </>,
    );
    expect(screen.getByRole("log", { name: "Journal du test" })).toHaveAttribute("tabindex", "0");
    const section = screen.getByRole("region", { name: "Différences" });
    expect(section.tagName).toBe("SECTION");
    expect(section).not.toHaveAttribute("role"); // rôle implicite, pas d'attribut redondant
    expect(screen.getByRole("region", { name: "Table large" }).tagName).toBe("DIV");
  });
});
