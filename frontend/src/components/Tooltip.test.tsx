import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Tooltip } from "./Tooltip";

describe("Tooltip", () => {
  it("shows on hover, ties the text to the button with aria-describedby, and hides on leave", async () => {
    render(<Tooltip text="Supprimer la colonne"><button type="button" aria-label="Supprimer">x</button></Tooltip>);
    const btn = screen.getByRole("button", { name: "Supprimer" });
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    await userEvent.hover(btn);
    const tip = screen.getByRole("tooltip");
    expect(tip).toHaveTextContent("Supprimer la colonne");
    expect(btn).toHaveAttribute("aria-describedby", tip.id);
    await userEvent.unhover(btn);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("Escape hides it", async () => {
    render(<Tooltip text="Aide"><button type="button">?</button></Tooltip>);
    await userEvent.hover(screen.getByRole("button"));
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});
