import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DisplayPicker, SideMargin } from "./DisplayPicker";

describe("Type d'affichage", () => {
  it("propose standard, pleine page et moderne ; standard est choisi par défaut", () => {
    render(<DisplayPicker value={undefined} onChange={() => undefined} />);
    expect(screen.getAllByRole("radio")).toHaveLength(3);
    for (const name of ["Standard", "Pleine page", "Moderne"]) expect(screen.getByRole("radio", { name: new RegExp(`^${name}`) })).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Standard/ })).toHaveAttribute("aria-checked", "true");
  });
  it("choisir un affichage le renvoie ; le choix en cours est coché", async () => {
    const onChange = vi.fn();
    render(<DisplayPicker value="page" onChange={onChange} />);
    expect(screen.getByRole("radio", { name: /Pleine page/ })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(screen.getByRole("radio", { name: /Moderne/ }));
    expect(onChange).toHaveBeenCalledWith("modern");
  });
  it("lecture seule : rien ne se choisit", async () => {
    const onChange = vi.fn();
    render(<DisplayPicker value="standard" onChange={onChange} disabled />);
    await userEvent.click(screen.getByRole("radio", { name: /Moderne/ }));
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("Marges latérales du mode moderne", () => {
  it("le curseur règle la marge ; « Automatique » la retire", async () => {
    const onChange = vi.fn();
    const { rerender } = render(<SideMargin value={undefined} onChange={onChange} />);
    expect(screen.getByText("Auto")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Automatique" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Marges latérales/), { target: { value: "15" } });
    expect(onChange).toHaveBeenCalledWith(15);
    rerender(<SideMargin value={15} onChange={onChange} />);
    expect(screen.getByText("15 %")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Automatique" }));
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });
  it("0 % : toute la largeur ; lecture seule : rien ne bouge", () => {
    render(<SideMargin value={0} onChange={() => undefined} disabled />);
    expect(screen.getByText("Le formulaire occupe toute la largeur.")).toBeTruthy();
    expect(screen.getByLabelText(/Marges latérales/)).toBeDisabled();
  });
});
