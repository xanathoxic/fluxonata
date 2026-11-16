import { act, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ControlInput } from "./ControlInput";
import type { LookupFn } from "./DefinitionForm";
import type { FormField } from "./types";

const field = {
  key: "contact",
  type: "custom",
  label: "Contact",
  control: { ref: "contact", spec: { version: 1, mode: "group", parts: [{ key: "nom", label: "Nom", type: "text" }] } },
  prefill: { connection: "svc", path: "/contact" },
} as unknown as FormField;

/** Hôte : à chaque réaffichage, le gestionnaire de valeur « voit » un compteur différent (comme un parent qui dépend d'autres champs). */
function Host({ lookup, log }: { lookup: LookupFn; log: string[] }) {
  const [tick, setTick] = useState(0);
  return (
    <>
      <button type="button" onClick={() => setTick((t) => t + 1)}>
        réafficher
      </button>
      <ControlInput field={field} id="c" value={undefined} lookup={lookup} onChange={(v) => log.push(`rendu ${tick} : ${JSON.stringify(v)}`)} />
    </>
  );
}

describe("pré-remplissage par un service : la valeur est remise au gestionnaire COURANT", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  it("un parent réaffiché pendant l'attente de la réponse reçoit le pré-remplissage", async () => {
    let resolve: (v: unknown) => void = () => undefined;
    const lookup = vi.fn<LookupFn>(() => new Promise((r) => { resolve = r; }));
    const log: string[] = [];
    render(<Host lookup={lookup} log={log} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(400); }); // la recherche part après 300 ms
    expect(lookup).toHaveBeenCalledTimes(1);
    expect(lookup.mock.calls[0]?.[2]).toBe("prefill");
    await act(async () => { screen.getByText("réafficher").click(); }); // le parent change de gestionnaire pendant l'attente
    await act(async () => { resolve({ nom: "Alice" }); await vi.advanceTimersByTimeAsync(10); });
    expect(log).toEqual(['rendu 1 : {"nom":"Alice"}']);
  });

  it("ne remplit pas une seconde fois pour un simple réaffichage", async () => {
    const lookup = vi.fn<LookupFn>(async () => ({ nom: "Alice" }));
    const log: string[] = [];
    render(<Host lookup={lookup} log={log} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    await act(async () => { screen.getByText("réafficher").click(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(lookup).toHaveBeenCalledTimes(1);
  });
});
