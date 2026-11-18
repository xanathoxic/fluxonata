import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { BoxModel } from "./BoxModel";

let out = { margin: "", padding: "" };
function Host({ margin = "", padding = "" }: { margin?: string; padding?: string }) {
  const [m, setM] = useState(margin);
  const [p, setP] = useState(padding);
  out = { margin: m, padding: p };
  return <BoxModel margin={m} padding={p} onMargin={setM} onPadding={setP} />;
}
const side = (box: "extérieure" | "intérieure", name: string) => screen.getByLabelText(`Marge ${box} : ${name}`) as HTMLInputElement;
const set = (el: HTMLElement, v: string) => fireEvent.change(el, { target: { value: v } });

describe("Marge et padding : deux boîtes", () => {
  it("lit les quatre côtés de chaque boîte", () => {
    render(<Host margin="4px 8px 12px 16px" padding="6px" />);
    expect([side("extérieure", "haut").value, side("extérieure", "droite").value, side("extérieure", "bas").value, side("extérieure", "gauche").value]).toEqual(
      ["4", "8", "12", "16"],
    );
    expect(side("intérieure", "gauche").value).toBe("6");
  });
  it("côtés liés : un seul réglage donne les quatre", () => {
    render(<Host />);
    set(side("intérieure", "haut"), "10");
    expect(out.padding).toBe("10px"); // quatre valeurs égales : une seule écrite
    expect(side("intérieure", "bas").value).toBe("10");
    expect(out.margin).toBe(""); // l'autre boîte n'est pas touchée
  });
  it("côtés indépendants : un seul côté change, les autres gardent leur valeur", async () => {
    render(<Host margin="8px" />);
    await userEvent.click(screen.getByRole("button", { name: "Marge extérieure : lier les quatre côtés" }));
    set(side("extérieure", "gauche"), "20");
    expect(out.margin).toBe("8px 8px 8px 20px");
    set(side("extérieure", "haut"), "0");
    expect(out.margin).toBe("0px 8px 8px 20px");
  });
  it("une marge peut être négative, un padding jamais", () => {
    render(<Host />);
    expect(side("extérieure", "haut").min).toBe("-200");
    expect(side("intérieure", "haut").min).toBe("0");
    set(side("extérieure", "haut"), "-6");
    expect(out.margin).toBe("-6px");
  });
  it("centrer : auto à gauche et à droite, sans toucher haut et bas ; recliquer retire l'auto", async () => {
    render(<Host margin="12px 0" />);
    await userEvent.click(screen.getByRole("button", { name: /centrer horizontalement/ }));
    expect(out.margin).toBe("12px auto");
    expect(side("extérieure", "gauche").placeholder).toBe("auto");
    expect(screen.getByRole("button", { name: /centrer horizontalement/ })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: /centrer horizontalement/ }));
    expect(out.margin).toBe("12px 0"); // gauche et droite reviennent à 0
  });
  it("changer l'unité convertit les côtés réglés sans changer les nombres", () => {
    render(<Host padding="8px 16px" />);
    fireEvent.change(screen.getByLabelText("Marge intérieure : unité"), { target: { value: "rem" } });
    expect(out.padding).toBe("8rem 16rem");
  });
  it("vider un côté le remet à zéro, pas à « vide »", () => {
    render(<Host padding="8px 16px" />);
    fireEvent.change(side("intérieure", "haut"), { target: { value: "" } });
    expect(out.padding).toBe("0 16px 8px 16px"); // le côté vidé vaut 0, les autres restent
  });
});
