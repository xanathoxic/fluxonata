import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAnchorFocus } from "./useAnchorFocus";

function Page({ hash, ready }: { hash: string; ready: boolean }) {
  useAnchorFocus(hash, "#publier", ready, "bump");
  return <input id="bump" aria-label="Type de changement" />;
}

describe("ancre #publier : focus une seule fois, une fois la page prête", () => {
  afterEach(() => vi.restoreAllMocks());
  const spyFocus = () => vi.spyOn(HTMLElement.prototype, "focus");

  it("ne fait rien tant que les versions ne sont pas chargées, puis donne le focus une fois", () => {
    const focus = spyFocus();
    Element.prototype.scrollIntoView = vi.fn();
    const { rerender } = render(<Page hash="#publier" ready={false} />);
    expect(focus).not.toHaveBeenCalled();
    rerender(<Page hash="#publier" ready />);
    expect(focus).toHaveBeenCalledTimes(1);
    rerender(<Page hash="#publier" ready />); // nouvel affichage identique : aucune répétition
    rerender(<Page hash="#publier" ready />);
    expect(focus).toHaveBeenCalledTimes(1);
  });

  it("ne réagit qu'à l'ancre demandée", () => {
    const focus = spyFocus();
    Element.prototype.scrollIntoView = vi.fn();
    render(<Page hash="#autre" ready />);
    render(<Page hash="" ready />);
    expect(focus).not.toHaveBeenCalled();
  });

  it("rejoue quand l'ancre revient après avoir changé", () => {
    const focus = spyFocus();
    Element.prototype.scrollIntoView = vi.fn();
    const { rerender } = render(<Page hash="#publier" ready />);
    rerender(<Page hash="#autre" ready />);
    rerender(<Page hash="#publier" ready />);
    expect(focus).toHaveBeenCalledTimes(2);
  });
});
