import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { ScheduleNote, ScheduleWhen } from "./ScheduleConfig";
import { defaultNode } from "./graphOps";
import type { GraphNode } from "./types";

const NOW = Date.UTC(2026, 10, 24, 12, 0, 0); // 24 nov. 2026, 12:00 UTC
const base: GraphNode = {
  id: "p",
  type: "schedule",
  name: "Programmer",
  workflow: "rapport",
  when: "at",
  at: "2026-12-01T08:30",
  timezone: "Europe/Paris",
  on_past: "fail",
  inputs: {},
};

function Harness({ initial, onChange }: { initial: GraphNode; onChange?: (n: GraphNode) => void }) {
  const [node, setNode] = useState(initial);
  return (
    <ScheduleWhen
      node={node}
      now={NOW}
      onUpdate={(patch) => {
        const next = { ...node, ...patch };
        setNode(next);
        onChange?.(next);
      }}
    />
  );
}

describe("Programmer : quand lancer", () => {
  it("montre ce qui va se passer pour une date future, dans le fuseau choisi", () => {
    render(<Harness initial={base} />);
    expect(screen.getByLabelText("Date et heure")).toHaveValue("2026-12-01T08:30");
    expect(screen.getByRole("status")).toHaveTextContent(/Sera lancé le .*1 décembre 2026.*08:30/);
    expect(screen.getByRole("status")).toHaveTextContent("dans 6 jours");
    expect(screen.getByRole("status")).toHaveTextContent("07:30 UTC");
  });

  it("prévient quand la date est déjà passée", () => {
    render(<Harness initial={{ ...base, at: "2026-01-01T08:00" }} />);
    expect(screen.getByRole("status")).toHaveTextContent("Cette date est déjà passée.");
  });

  it("change la date et le fuseau, et recalcule l'aperçu", async () => {
    const seen = vi.fn();
    render(<Harness initial={base} onChange={seen} />);
    const user = userEvent.setup();
    const tz = screen.getByLabelText("Fuseau horaire");
    await user.clear(tz);
    await user.type(tz, "UTC");
    expect(seen).toHaveBeenLastCalledWith(expect.objectContaining({ timezone: "UTC" }));
    expect(screen.getByRole("status")).toHaveTextContent("(UTC)");
    expect(screen.getByRole("status")).not.toHaveTextContent("soit"); // même fuseau : pas de conversion à montrer
    await user.type(tz, "x"); // « UTCx » : fuseau inconnu
    expect(screen.getByText(/Fuseau inconnu/)).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull(); // pas d'aperçu trompeur avec un fuseau invalide
  });

  it("passe en date calculée (expression) sans aperçu, et revient à une date vide", async () => {
    const seen = vi.fn();
    render(<Harness initial={base} onChange={seen} />);
    const user = userEvent.setup();
    await user.click(screen.getByLabelText("Date calculée (expression)"));
    expect(seen).toHaveBeenLastCalledWith(expect.objectContaining({ at: "{{ inputs.date }}" }));
    expect(screen.getByLabelText("Date et heure (expression)")).toHaveValue("{{ inputs.date }}");
    expect(screen.queryByRole("status")).toBeNull();
    await user.click(screen.getByLabelText("Date calculée (expression)"));
    expect(screen.getByLabelText("Date et heure")).toHaveAttribute("type", "datetime-local");
  });

  it("propose un délai en unités, et le range en secondes", async () => {
    const seen = vi.fn();
    render(<Harness initial={{ ...base, when: "in", delay_seconds: 7200 }} onChange={seen} />);
    const user = userEvent.setup();
    expect(screen.getByLabelText("Délai")).toHaveValue(2);
    expect(screen.getByLabelText("Unité du délai")).toHaveValue("3600");
    const field = screen.getByLabelText("Délai");
    await user.clear(field);
    await user.type(field, "3");
    expect(seen).toHaveBeenLastCalledWith(expect.objectContaining({ delay_seconds: 10800 }));
    await user.selectOptions(screen.getByLabelText("Unité du délai"), "86400");
    expect(seen).toHaveBeenLastCalledWith(expect.objectContaining({ delay_seconds: 3 * 86400 }));
  });

  it("choisit quoi faire d'une date passée", async () => {
    const seen = vi.fn();
    render(<Harness initial={base} onChange={seen} />);
    await userEvent.selectOptions(screen.getByLabelText("Si la date est déjà passée"), "now");
    expect(seen).toHaveBeenLastCalledWith(expect.objectContaining({ on_past: "now" }));
  });

  it("bascule entre date précise et délai", async () => {
    render(<Harness initial={base} />);
    await userEvent.selectOptions(screen.getByLabelText("Quand lancer"), "in");
    expect(screen.queryByLabelText("Fuseau horaire")).toBeNull();
    expect(screen.getByLabelText("Délai")).toBeInTheDocument();
  });
});

describe("Programmer : étape neuve et rappel", () => {
  it("part d'une date valide (demain 08:00, Europe/Paris) et d'un échec si la date est passée", () => {
    const n = defaultNode({ inputs: { type: "object", properties: {} }, nodes: [], edges: [], outputs: {} }, "schedule");
    expect(n).toMatchObject({ type: "schedule", when: "at", timezone: "Europe/Paris", on_past: "fail", workflow: "", inputs: {} });
    expect(n.at).toMatch(/^\d{4}-\d{2}-\d{2}T08:00$/);
  });
  it("dit que l'étape ne lance rien maintenant", () => {
    render(<ScheduleNote node={base} />);
    expect(screen.getByText(/ne lance rien maintenant/)).toBeInTheDocument();
    expect(screen.getByText(/Lancements programmés/)).toBeInTheDocument();
  });
});
