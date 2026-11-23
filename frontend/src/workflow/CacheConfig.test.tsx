import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CacheConfig } from "./CacheConfig";
import type { GraphNode } from "./types";

const base: GraphNode = { id: "c", type: "cache", name: "Cache", operation: "read", key: "inv" };
const setup = (node: Partial<GraphNode> = {}) => {
  const onUpdate = vi.fn();
  render(<CacheConfig node={{ ...base, ...node }} onUpdate={onUpdate} />);
  return onUpdate;
};

describe("CacheConfig", () => {
  it("lecture : âge maximal et comportement si absent, pas de valeur ni de durée de vie", () => {
    setup();
    expect(screen.getByLabelText("Âge maximal accepté (secondes, facultatif)")).toBeTruthy();
    expect(screen.getByLabelText("Si aucune valeur")).toBeTruthy();
    expect(screen.queryByLabelText("Valeur à écrire")).toBeNull();
    expect(screen.queryByLabelText("Durée de vie (secondes, facultatif)")).toBeNull();
  });

  it("écriture : valeur et durée de vie, pas de réglages de lecture", () => {
    setup({ operation: "write" });
    expect(screen.getByLabelText("Valeur à écrire")).toBeTruthy();
    expect(screen.getByLabelText("Durée de vie (secondes, facultatif)")).toBeTruthy();
    expect(screen.queryByLabelText("Si aucune valeur")).toBeNull();
  });

  it("suppression : seulement l'opération et la clé", () => {
    setup({ operation: "delete" });
    expect(screen.getByLabelText("Clé")).toBeTruthy();
    expect(screen.queryByLabelText("Valeur à écrire")).toBeNull();
    expect(screen.queryByLabelText("Âge maximal accepté (secondes, facultatif)")).toBeNull();
  });

  it("la clé et l'opération sont transmises", () => {
    const onUpdate = setup();
    fireEvent.change(screen.getByLabelText("Clé"), { target: { value: "autre" } });
    expect(onUpdate).toHaveBeenCalledWith({ key: "autre" });
    fireEvent.change(screen.getByLabelText("Opération"), { target: { value: "write" } });
    expect(onUpdate).toHaveBeenCalledWith({ operation: "write" });
  });

  it("une valeur JSON est enregistrée comme JSON, une expression comme texte", () => {
    const onUpdate = setup({ operation: "write" });
    fireEvent.change(screen.getByLabelText("Valeur à écrire"), { target: { value: '{"a": [1, 2]}' } });
    expect(onUpdate).toHaveBeenLastCalledWith({ value: { a: [1, 2] } });
    fireEvent.change(screen.getByLabelText("Valeur à écrire"), { target: { value: "{{ steps.x.output.rows }}" } });
    expect(onUpdate).toHaveBeenLastCalledWith({ value: "{{ steps.x.output.rows }}" });
    fireEvent.change(screen.getByLabelText("Valeur à écrire"), { target: { value: "{ cassé" } });
    expect(onUpdate).toHaveBeenLastCalledWith({ value: "{ cassé" });
  });

  it("les durées sont des entiers, ou le texte tapé tant qu'il n'est pas valide, ou vide", () => {
    const onUpdate = setup({ operation: "write", ttl_seconds: 60 });
    const ttl = screen.getByLabelText("Durée de vie (secondes, facultatif)");
    fireEvent.change(ttl, { target: { value: "3600" } });
    expect(onUpdate).toHaveBeenLastCalledWith({ ttl_seconds: 3600 });
    fireEvent.change(ttl, { target: { value: "1h" } });
    expect(onUpdate).toHaveBeenLastCalledWith({ ttl_seconds: "1h" });
    fireEvent.change(ttl, { target: { value: "" } });
    expect(onUpdate).toHaveBeenLastCalledWith({ ttl_seconds: "" });
  });

  it("reconstruire : les entrées du workflow producteur se saisissent en JSON et se vident", () => {
    const onUpdate = setup({ on_missing: "rebuild", rebuild_inputs: { site: "lyon" } });
    const box = screen.getByLabelText("Entrées du workflow producteur (JSON, facultatif)");
    fireEvent.change(box, { target: { value: '{"site": "paris"}' } });
    expect(onUpdate).toHaveBeenLastCalledWith({ rebuild_inputs: { site: "paris" } });
    fireEvent.change(box, { target: { value: "" } });
    expect(onUpdate).toHaveBeenLastCalledWith({ rebuild_inputs: undefined });
  });

  it("sans « reconstruire », pas de champ d'entrées ; l'option est proposée", () => {
    setup();
    expect(screen.queryByLabelText("Entrées du workflow producteur (JSON, facultatif)")).toBeNull();
    expect(screen.getByRole("option", { name: "Reconstruire avec le workflow producteur" })).toBeTruthy();
  });

  it("écriture : la case « Valeur sensible » est transmise, absente en lecture", () => {
    const onUpdate = setup({ operation: "write" });
    fireEvent.click(screen.getByLabelText("Valeur sensible"));
    expect(onUpdate).toHaveBeenLastCalledWith({ sensitive: true });
  });

  it("lecture : pas de case « Valeur sensible »", () => {
    setup();
    expect(screen.queryByLabelText("Valeur sensible")).toBeNull();
  });
});
