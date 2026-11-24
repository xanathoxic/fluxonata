import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fireEvent } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { NoteConfig } from "./NoteConfig";
import type { Note } from "./types";

const base: Note = { id: "note_1", text: "À savoir", ui: { x: 10, y: 20 } };

function Harness({
  initial = base,
  editable = true,
  onChange,
  onDelete = vi.fn(),
  onClose = vi.fn(),
}: {
  initial?: Note;
  editable?: boolean;
  onChange?: (n: Note) => void;
  onDelete?: () => void;
  onClose?: () => void;
}) {
  const [note, setNote] = useState(initial);
  return (
    <NoteConfig
      note={note}
      editable={editable}
      onChange={(patch) => {
        const next = { ...note, ...patch };
        setNote(next);
        onChange?.(next);
      }}
      onDelete={onDelete}
      onClose={onClose}
    />
  );
}

describe("Propriétés d'une note", () => {
  it("modifie le texte", async () => {
    let last = base;
    render(<Harness onChange={(n) => (last = n)} />);
    await userEvent.type(screen.getByLabelText("Texte de la note"), "!");
    expect(last.text).toBe("À savoir!");
  });

  it("propose sept couleurs nommées ; la couleur d'origine est celle de départ, et la rechoisir efface le réglage", async () => {
    let last = base;
    render(<Harness onChange={(n) => (last = n)} />);
    const group = screen.getByRole("radiogroup", { name: "Couleur de la note" });
    expect(group.querySelectorAll('[role="radio"]')).toHaveLength(7);
    expect(screen.getByRole("radio", { name: "Jaune" })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: "Bleu" }));
    expect(last.color).toBe("#bfdbfe");
    expect(screen.getByRole("radio", { name: "Bleu" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Jaune" })).not.toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: "Jaune" }));
    expect(last.color).toBeUndefined();
  });

  it("accepte n'importe quelle couleur par le sélecteur", () => {
    let last = base;
    render(<Harness onChange={(n) => (last = n)} />);
    fireEvent.change(screen.getByLabelText("Autre couleur"), { target: { value: "#123abc" } });
    expect(last.color).toBe("#123abc");
  });

  it("règle l'opacité du fond de 10 à 100 % et montre un aperçu ; 100 % efface le réglage", () => {
    let last = base;
    render(<Harness onChange={(n) => (last = n)} />);
    const slider = screen.getByLabelText(/Opacité du fond/);
    expect((slider as HTMLInputElement).value).toBe("100");
    expect(slider).toHaveAttribute("min", "10");
    fireEvent.change(slider, { target: { value: "40" } });
    expect(last.opacity).toBe(0.4);
    expect(screen.getByLabelText("Opacité du fond : 40 %")).toBeInTheDocument();
    expect(screen.getByText("Aperçu du fond et du texte")).toHaveStyle("background-color: rgba(255, 243, 168, 0.4)");
    fireEvent.change(slider, { target: { value: "100" } });
    expect(last.opacity).toBeUndefined();
  });

  it("règle la taille : largeur et hauteur bornées, vides = automatique, et garde la position", async () => {
    let last: Note = { ...base, ui: { x: 10, y: 20, w: 200, h: 100 } };
    render(<Harness initial={last} onChange={(n) => (last = n)} />);
    expect(screen.getByLabelText("Largeur")).toHaveValue(200);
    fireEvent.change(screen.getByLabelText("Largeur"), { target: { value: "320" } });
    expect(last.ui).toEqual({ x: 10, y: 20, w: 320, h: 100 });
    fireEvent.change(screen.getByLabelText("Hauteur"), { target: { value: "5" } });
    expect(last.ui.h).toBe(44); // borné au minimum
    fireEvent.change(screen.getByLabelText("Largeur"), { target: { value: "99999" } });
    expect(last.ui.w).toBe(2000);
    fireEvent.change(screen.getByLabelText("Largeur"), { target: { value: "" } });
    expect(last.ui).toEqual({ x: 10, y: 20, h: 44 });
  });

  it("une note sans taille affiche « auto »", () => {
    render(<Harness />);
    expect(screen.getByLabelText("Largeur")).toHaveAttribute("placeholder", "auto");
    expect(screen.getByLabelText("Largeur")).toHaveValue(null);
  });

  it("supprime et ferme", async () => {
    const onDelete = vi.fn();
    const onClose = vi.fn();
    render(<Harness onDelete={onDelete} onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "Supprimer la note" }));
    await userEvent.click(screen.getByRole("button", { name: "Fermer" }));
    expect(onDelete).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("en lecture seule, tout est désactivé", () => {
    render(<Harness editable={false} />);
    expect(screen.getByLabelText("Texte de la note")).toBeDisabled();
    expect(screen.getByRole("radio", { name: "Bleu" })).toBeDisabled();
    expect(screen.getByLabelText(/Opacité du fond/)).toBeDisabled();
    expect(screen.getByLabelText("Largeur")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Supprimer la note" })).toBeDisabled();
  });

  it("une couleur enregistrée invalide est ignorée : l'aperçu reprend la couleur d'origine", () => {
    render(<Harness initial={{ ...base, color: "red; background:url(x)", opacity: 7 }} />);
    expect(screen.getByRole("radio", { name: "Jaune" })).toBeChecked();
    expect(screen.getByLabelText("Opacité du fond : 100 %")).toBeInTheDocument();
  });
});
