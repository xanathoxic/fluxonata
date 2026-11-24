import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { WaitUntil } from "./ScheduleConfig";
import { summarize } from "./graphOps";
import type { GraphNode } from "./types";

const NOW = Date.UTC(2026, 10, 24, 12, 0, 0);
const wait: GraphNode = { id: "w", type: "wait", name: "Pause", seconds: 60 };

function Harness({ initial, deadline, onChange }: { initial: GraphNode; deadline?: boolean; onChange?: (n: GraphNode) => void }) {
  const [node, setNode] = useState(initial);
  return (
    <WaitUntil
      node={node}
      now={NOW}
      deadline={deadline}
      onUpdate={(patch) => {
        const next = { ...node, ...patch };
        setNode(next);
        onChange?.(next);
      }}
    />
  );
}

describe("Attente : pause jusqu'à une date", () => {
  it("garde le réglage par durée par défaut", () => {
    render(<Harness initial={wait} />);
    expect(screen.getByLabelText("Durée (secondes)")).toHaveValue(60);
    expect(screen.queryByLabelText("Fuseau horaire")).toBeNull();
  });

  it("bascule vers une date, propose demain 8 h dans le fuseau par défaut, et revient à la durée", async () => {
    let last = wait;
    render(<Harness initial={wait} onChange={(n) => (last = n)} />);
    await userEvent.selectOptions(screen.getByLabelText("Mettre en pause"), "until");
    expect(last.until).toMatch(/^\d{4}-\d{2}-\d{2}T08:00$/);
    expect(last.timezone).toBe("Europe/Paris");
    await userEvent.selectOptions(screen.getByLabelText("Mettre en pause"), "for");
    expect(last.until).toBeUndefined();
  });

  it("annonce la reprise dans le fuseau choisi, et prévient d'une date passée", () => {
    const { unmount } = render(<Harness initial={{ ...wait, until: "2026-12-01T08:30", timezone: "Europe/Paris" }} />);
    expect(screen.getByRole("status")).toHaveTextContent(/Reprise le .*1 décembre 2026.*07:30 UTC/);
    unmount();
    render(<Harness initial={{ ...wait, until: "2026-11-01T08:30", timezone: "UTC" }} />);
    expect(screen.getByRole("status")).toHaveTextContent("déjà passée");
  });

  it("accepte une expression (pas d'aperçu) et le choix « continuer tout de suite »", async () => {
    let last = wait;
    render(<Harness initial={{ ...wait, until: "{{ inputs.date }}", timezone: "UTC" }} onChange={(n) => (last = n)} />);
    expect(screen.queryByRole("status")).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText("Si la date est déjà passée"), "now");
    expect(last.on_past).toBe("now");
  });

  it("signale un fuseau inconnu", () => {
    render(<Harness initial={{ ...wait, until: "2026-12-01T08:30", timezone: "Mars/Base" }} />);
    expect(screen.getByText(/Fuseau inconnu/)).toBeInTheDocument();
  });

  it("parle de délai maximum quand l'attente peut aussi être reprise par appel ou formulaire", () => {
    render(<Harness initial={{ ...wait, resume: "webhook" }} deadline />);
    expect(screen.getByLabelText("Délai maximum")).toBeInTheDocument();
  });
});

describe("Attente : résumé sur le schéma", () => {
  it("dit la date de reprise", () => {
    expect(summarize({ ...wait, until: "2026-12-01T08:30" }, [])).toBe("Pause jusqu'au 2026-12-01 à 08:30");
    expect(summarize(wait, [])).toBe("Attendre 60 s");
  });
});
