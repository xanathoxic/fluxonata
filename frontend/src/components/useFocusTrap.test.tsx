import { act, render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import { describe, expect, it } from "vitest";
import { useFocusTrap } from "./useFocusTrap";

function Dialog({ log }: { log: string[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const [version, setVersion] = useState(1);
  useFocusTrap(ref, () => log.push(`fermé par la version ${version}`));
  return (
    <div ref={ref} role="dialog" aria-label="Essai" tabIndex={-1}>
      <button type="button" onClick={() => setVersion((v) => v + 1)}>
        changer
      </button>
    </div>
  );
}

describe("piège à focus : Échap appelle la version courante de la fermeture", () => {
  it("la fermeture rendue après l'ouverture est celle qui est appelée", async () => {
    const log: string[] = [];
    const { getByText } = render(<Dialog log={log} />);
    await userEvent.click(getByText("changer")); // le parent se réaffiche avec une nouvelle fermeture
    await userEvent.keyboard("{Escape}");
    expect(log).toEqual(["fermé par la version 2"]);
  });

  it("n'appelle rien sans Échap et ne s'installe qu'une fois", async () => {
    const log: string[] = [];
    const { getByText } = render(<Dialog log={log} />);
    await userEvent.click(getByText("changer"));
    await userEvent.click(getByText("changer"));
    expect(log).toEqual([]);
    await act(async () => { await userEvent.keyboard("{Escape}"); });
    expect(log).toHaveLength(1); // un seul écouteur, pas un par rendu
  });
});

function Opener() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        ouvrir
      </button>
      {open && <Dialog2 onClose={() => setOpen(false)} />}
    </>
  );
}
function Dialog2({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, onClose);
  return (
    <div ref={ref} role="dialog" aria-label="Nouveau" tabIndex={-1}>
      <label htmlFor="nom">Nom</label>
      {/* biome-ignore lint/a11y/noAutofocus: le test vérifie précisément qu'un autoFocus du contenu d'un dialogue est respecté */}
      <input id="nom" autoFocus />
      <button type="button" onClick={onClose}>
        fermer
      </button>
    </div>
  );
}

describe("piège à focus : focus initial et retour à l'ouvreur", () => {
  it("un champ avec autoFocus garde le focus à l'ouverture (il n'est pas écrasé par le dialogue)", async () => {
    const { getByText, getByLabelText } = render(<Opener />);
    await userEvent.click(getByText("ouvrir"));
    expect(getByLabelText("Nom")).toHaveFocus();
  });

  it("sans autoFocus, le dialogue lui-même reçoit le focus", () => {
    const { getByRole } = render(<Dialog log={[]} />);
    expect(getByRole("dialog")).toHaveFocus();
  });

  it("à la fermeture, le focus revient au bouton qui a ouvert le dialogue, même avec un autoFocus", async () => {
    const { getByText } = render(<Opener />);
    const opener = getByText("ouvrir");
    await userEvent.click(opener);
    await userEvent.click(getByText("fermer"));
    expect(opener).toHaveFocus();
  });

  it("Échap ferme le dialogue et rend le focus à l'ouvreur", async () => {
    const { getByText } = render(<Opener />);
    const opener = getByText("ouvrir");
    await userEvent.click(opener);
    await userEvent.keyboard("{Escape}");
    expect(opener).toHaveFocus();
  });
});
