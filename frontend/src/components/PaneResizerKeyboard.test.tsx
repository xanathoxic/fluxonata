import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { PaneResizer } from "../workflow/PaneResizer";

function setup(side: "left" | "right" = "left") {
  const seen: number[] = [];
  render(<PaneResizer side={side} value={300} min={200} max={500} label="Largeur du volet" def={320} onChange={(v) => seen.push(v)} />);
  const sep = screen.getByRole("separator", { name: "Largeur du volet" });
  return { sep, seen };
}

describe("séparateur de volets au clavier (motif « séparateur de volet »)", () => {
  it("expose son orientation, sa valeur et ses bornes, et reçoit le focus", async () => {
    const { sep } = setup();
    expect(sep).toHaveAttribute("aria-orientation", "vertical");
    expect([sep.getAttribute("aria-valuenow"), sep.getAttribute("aria-valuemin"), sep.getAttribute("aria-valuemax")]).toEqual(["300", "200", "500"]);
    await userEvent.tab();
    expect(sep).toHaveFocus();
  });

  it("les flèches changent la largeur par pas (Maj : pas plus grand), bornée ; Début revient à la valeur par défaut", async () => {
    const { sep, seen } = setup();
    sep.focus();
    await userEvent.keyboard("{ArrowRight}{ArrowLeft}{Shift>}{ArrowRight}{/Shift}{Home}");
    expect(seen).toEqual([316, 284, 348, 320]);
  });

  it("le volet de droite grandit dans le sens inverse", async () => {
    const { sep, seen } = setup("right");
    sep.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(seen).toEqual([284]);
  });

  it("ne dépasse jamais les bornes", async () => {
    const seen: number[] = [];
    render(<PaneResizer side="left" value={495} min={200} max={500} label="L" def={320} onChange={(v) => seen.push(v)} />);
    screen.getByRole("separator").focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(seen).toEqual([500]);
  });
});
