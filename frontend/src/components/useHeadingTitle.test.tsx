import { act, render } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { titleFor, useHeadingTitle } from "./useHeadingTitle";

function Page() {
  useHeadingTitle();
  const [heading, setHeading] = useState("Mes exécutions");
  return (
    <main>
      <h1>{heading}</h1>
      <button type="button" onClick={() => setHeading("Workflows")}>
        changer
      </button>
    </main>
  );
}

const frame = () => new Promise((r) => requestAnimationFrame(() => r(null)));

describe("titre du document", () => {
  it("se déduit du titre de niveau 1, avec un repli sur le nom de l'application", () => {
    expect(titleFor("Mes exécutions")).toBe("Mes exécutions — Fluxonata");
    expect(titleFor("  Mes   exécutions \n")).toBe("Mes exécutions — Fluxonata");
    expect(titleFor("")).toBe("Fluxonata");
    expect(titleFor(null)).toBe("Fluxonata");
    expect(titleFor("Fluxonata")).toBe("Fluxonata");
  });

  it("suit le titre de la page affichée, y compris quand il change", async () => {
    const { getByText } = render(<Page />);
    expect(document.title).toBe("Mes exécutions — Fluxonata");
    act(() => getByText("changer").click());
    await act(async () => { await frame(); await frame(); });
    expect(document.title).toBe("Workflows — Fluxonata");
  });
});
