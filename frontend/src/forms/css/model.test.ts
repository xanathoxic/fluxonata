import { describe, expect, it } from "vitest";
import { getDecl, parse, ruleKey, serialize, setDecl, setMany } from "./model";
import { CUSTOM, defaultTarget, describe as describeRule, ruleOf, selectorsFor, targetFromElement, targetOf, type Target } from "./targets";
import { formatBox, formatColor, formatShadows, parseBorder, parseBox, parseColor, parseGradient, parseShadows } from "./values";

// the text the server writes for this stylesheet (triggers/formcss.py)
const CANON = `& .fb-field > label,
& .fb-field > .fb-label {
  color: #334155;
  font-weight: 700;
}

& input[type="text"]:hover {
  border-color: #1d4ed8;
}

@media (max-width: 600px) {
  & .fb-section {
    padding: 8px;
  }
  & .fb-heading {
    font-size: 1.2rem;
  }
}

@media (prefers-color-scheme: dark) {
  & .fb-paragraph {
    color: #cbd5e1;
  }
}`;

describe("parse / serialize", () => {
  it("reads the canonical text and writes it back unchanged", () => {
    const rules = parse(CANON);
    expect(rules).toHaveLength(5);
    expect(rules?.[0]).toEqual({ selectors: ["& .fb-field > label", "& .fb-field > .fb-label"], media: null, decls: [["color", "#334155"], ["font-weight", "700"]] });
    expect(rules?.[2]?.media).toBe("(max-width: 600px)");
    expect(serialize(rules ?? [])).toBe(CANON);
  });
  it("handles empty text, strings holding punctuation, and refuses what is not canonical", () => {
    expect(parse("")).toEqual([]);
    expect(parse(undefined)).toEqual([]);
    const withString = '& h3::before {\n  content: "a; b } c";\n}';
    expect(parse(withString)?.[0]?.decls[0]).toEqual(["content", '"a; b } c"']);
    expect(serialize(parse(withString) ?? [])).toBe(withString);
    expect(parse(".a { color: red")).toBeNull();
    expect(parse("& .a { & .b { color: red } }")).toBeNull();
  });
  it("drops rules without declarations", () => {
    expect(serialize([{ selectors: ["& .a"], media: null, decls: [] }])).toBe("");
  });
});

describe("setDecl", () => {
  const at = { selectors: ["& .a"], media: null };
  it("creates, updates and removes declarations, and the rule with the last one", () => {
    let r = setDecl([], at, "color", "red");
    expect(r).toEqual([{ ...at, decls: [["color", "red"]] }]);
    r = setDecl(r, at, "opacity", ".8");
    r = setDecl(r, at, "color", "blue");
    expect(r[0]?.decls).toEqual([["color", "blue"], ["opacity", ".8"]]);
    r = setDecl(r, at, "color", "");
    expect(getDecl(r[0], "color")).toBe("");
    r = setDecl(r, at, "opacity", "");
    expect(r).toEqual([]);
    expect(setDecl([], at, "color", "")).toEqual([]); // nothing to remove: no empty rule is created
  });
  it("keeps rules of other screens apart", () => {
    const m = { selectors: ["& .a"], media: "(max-width: 600px)" };
    const r = setDecl(setDecl([], at, "color", "red"), m, "color", "blue");
    expect(r).toHaveLength(2);
    expect(ruleKey(r[0] as never)).not.toBe(ruleKey(r[1] as never));
    expect(setMany([], at, { margin: "0", padding: "" })).toEqual([{ ...at, decls: [["margin", "0"]] }]);
  });
});

describe("targets", () => {
  it("writes selectors the server's grammar accepts, and reads them back", () => {
    const cases: Target[] = [
      defaultTarget(),
      { scope: { kind: "all" }, part: "input", state: "focus-visible", screen: "all" },
      { scope: { kind: "all" }, part: "button", state: "hover", screen: "mobile" },
      { scope: { kind: "key", key: "nom" }, part: "label", state: "normal", screen: "all" },
      { scope: { kind: "key", key: "nom" }, part: "cell", state: "normal", screen: "dark" },
      { scope: { kind: "type", type: "select" }, part: "input", state: "invalid", screen: "all" },
      { scope: { kind: "all" }, part: "root", state: "hover", screen: "all" },
      { scope: { kind: "all" }, part: "input", state: "placeholder", screen: "all" },
    ];
    for (const t of cases) {
      const back = targetOf(ruleOf(t));
      expect(back).toEqual(t);
      expect(selectorsFor(t).every((s) => s.startsWith("&"))).toBe(true);
    }
    expect(selectorsFor({ scope: { kind: "key", key: "nom" }, part: "cell", state: "normal", screen: "all" })).toEqual(['& [data-key="nom"]']);
    expect(selectorsFor({ scope: { kind: "key", key: "nom" }, part: "label", state: "normal", screen: "all" })).toEqual(['& [data-key="nom"] > .fb-field > label', '& [data-key="nom"] > .fb-field > .fb-label']); // le libellé du champ LUI-MÊME, pas ceux des champs imbriqués
  });
  it("keeps hand-written selectors as custom targets", () => {
    const t = targetOf({ selectors: ["& .fb-actions > button + button"], media: null });
    expect(t.part).toBe(CUSTOM);
    expect(selectorsFor(t)).toEqual(["& .fb-actions > button + button"]);
    expect(describeRule({ selectors: ["& .fb-actions > button + button"], media: null })).toContain("button + button");
  });
  it("describes a rule in words", () => {
    expect(describeRule({ selectors: ['& [data-key="nom"] > .fb-field > label', '& [data-key="nom"] > .fb-field > .fb-label'], media: "(max-width: 600px)" })).toBe("Libellés · champ nom · Mobile (jusqu'à 600 px)");
  });
});

describe("values", () => {
  it("reads and writes box shorthands", () => {
    expect(parseBox("8px")).toEqual(["8px", "8px", "8px", "8px"]);
    expect(parseBox("4px 12px")).toEqual(["4px", "12px", "4px", "12px"]);
    expect(parseBox("1px 2px 3px")).toEqual(["1px", "2px", "3px", "2px"]);
    expect(formatBox(["4px", "12px", "4px", "12px"])).toBe("4px 12px");
    expect(formatBox(["8px", "8px", "8px", "8px"])).toBe("8px");
    expect(formatBox(["1px", "2px", "3px", "4px"])).toBe("1px 2px 3px 4px");
    expect(formatBox(["", "", "", ""])).toBe("");
  });
  it("reads and writes colours", () => {
    expect(parseColor("#1d4ed8")).toEqual({ r: 29, g: 78, b: 216, a: 1 });
    expect(parseColor("#fff")).toEqual({ r: 255, g: 255, b: 255, a: 1 });
    expect(parseColor("rgba(0, 0, 0, .25)")).toEqual({ r: 0, g: 0, b: 0, a: 0.25 });
    expect(parseColor("var(--accent)")).toBeNull();
    expect(formatColor({ r: 29, g: 78, b: 216, a: 1 })).toBe("#1d4ed8");
    expect(formatColor({ r: 0, g: 0, b: 0, a: 0.25 })).toBe("rgba(0, 0, 0, 0.25)");
  });
  it("reads and writes shadows, gradients and borders", () => {
    const s = parseShadows("0 2px 8px 0 rgba(0, 0, 0, .2), inset 0 1px 0 #fff");
    expect(s).toHaveLength(2);
    expect(s?.[1]).toMatchObject({ inset: true, y: 1, color: "#fff" });
    expect(formatShadows(s ?? [])).toBe("0px 2px 8px 0px rgba(0, 0, 0, .2), inset 0px 1px 0px 0px #fff");
    expect(parseShadows("none")).toEqual([]);
    expect(parseGradient("linear-gradient(135deg, #fff, #eef)")).toEqual({ angle: 135, from: "#fff", to: "#eef" });
    expect(parseGradient("red")).toBeNull();
    expect(parseBorder("2px dashed #1d4ed8")).toEqual({ w: "2px", s: "dashed", c: "#1d4ed8" });
  });
});

describe("Portée « dans un contrôle »", () => {
  const T = (scope: Target["scope"], part: string, state = "normal"): Target => ({ scope, part, state, screen: "all" });
  it("vise l'intérieur d'un contrôle, ou un seul de ses sous-champs, sans toucher à son propre libellé", () => {
    expect(selectorsFor(T({ kind: "in", key: "adr" }, "label"))).toEqual(['& [data-key="adr"] .ctl .fb-field > label', '& [data-key="adr"] .ctl .fb-field > .fb-label']);
    expect(selectorsFor(T({ kind: "in", key: "adr", sub: "ville" }, "input", "focus"))[0]).toBe('& [data-key="adr"] .ctl [data-key="ville"] input[type="text"]:focus');
    expect(selectorsFor(T({ kind: "in", key: "adr", sub: "ville" }, "cell"))).toEqual(['& [data-key="adr"] .ctl [data-key="ville"]']);
    expect(selectorsFor(T({ kind: "in", key: "adr" }, "cell"))).toEqual(['& [data-key="adr"] .ctl .fb-cell']);
    expect(selectorsFor(T({ kind: "key", key: "lignes" }, "ctlRowHead"))).toEqual(['& [data-key="lignes"] .ctl-rowhead']);
    expect(selectorsFor(T({ kind: "all" }, "fold"))).toEqual([".ctl-fold"].map((s) => `& ${s}`));
  });
  it("une règle écrite relit sa cible, et la décrit", () => {
    for (const t of [T({ kind: "in", key: "adr" }, "label"), T({ kind: "in", key: "adr", sub: "ville" }, "input", "hover"), T({ kind: "in", key: "adr", sub: "ville" }, "cell"), T({ kind: "key", key: "adr" }, "ctl")]) {
      const back = targetOf(ruleOf(t));
      expect(back.part).toBe(t.part);
      expect(back.scope).toEqual(t.scope);
    }
    expect(describeRule(ruleOf(T({ kind: "in", key: "adr", sub: "ville" }, "label")))).toBe("Libellés · dans adr › ville");
  });
  it("les cibles d'avant (champ, type, tout) se relisent comme avant", () => {
    expect(targetOf(ruleOf(T({ kind: "key", key: "nom" }, "label"))).scope).toEqual({ kind: "key", key: "nom" });
    expect(targetOf(ruleOf(T({ kind: "type", type: "text" }, "input"))).scope).toEqual({ kind: "type", type: "text" });
    expect(targetOf(ruleOf(T({ kind: "all" }, "label"))).scope).toEqual({ kind: "all" });
  });
});

describe("Cliquer dans l'aperçu : l'élément le plus proche", () => {
  const html = `<form class="fb-form"><div class="fb-cell" data-key="adr" data-type="custom"><div class="fb-field"><label>Adresse</label>
    <div class="ctl"><div class="ctl-row"><div class="ctl-rowhead"><button class="ctl-fold">v</button></div>
    <div class="fb-cell" data-key="ville" data-type="text"><div class="fb-field"><label>Ville</label><input type="text"/></div></div></div></div></div></div></form>`;
  const dom = () => { const d = document.createElement("div"); d.innerHTML = html; return d; };
  it("le libellé d'un sous-champ est un libellé, et le contrôle qui le contient est reconnu", () => {
    const d = dom();
    const root = d.querySelector(".fb-form") as Element;
    const hit = targetFromElement(d.querySelector(".ctl-row label") as Element, root);
    expect(hit).toMatchObject({ part: "label", key: "ville", type: "text", control: "adr" });
    expect(targetFromElement(d.querySelector(".ctl-fold") as Element, root)?.part).toBe("fold");
    expect(targetFromElement(d.querySelector(".ctl-rowhead") as Element, root)?.part).toBe("ctlRowHead");
    expect(targetFromElement(d.querySelector(".ctl-row") as Element, root)?.part).toBe("ctlRow");
    expect(targetFromElement(d.querySelector(".ctl") as Element, root)?.part).toBe("ctl");
  });
  it("un champ ordinaire n'a pas de contrôle autour de lui", () => {
    const d = dom();
    const root = d.querySelector(".fb-form") as Element;
    expect(targetFromElement(d.querySelector("label") as Element, root)).toMatchObject({ part: "label", key: "adr", control: undefined });
  });
});

describe("Un champ, un type ou un sous-champ : ce qui lui appartient, pas ses enfants", () => {
  const T = (scope: Target["scope"], part: string): Target => ({ scope, part, state: "normal", screen: "all" });
  it("le libellé, l'aide, l'erreur et l'astérisque d'un champ ne débordent pas sur les champs imbriqués", () => {
    expect(selectorsFor(T({ kind: "key", key: "adr" }, "help"))).toEqual(['& [data-key="adr"] > .fb-field > .hint']);
    expect(selectorsFor(T({ kind: "key", key: "adr" }, "error"))).toEqual(['& [data-key="adr"] > .fb-field > .field-error']);
    expect(selectorsFor(T({ kind: "type", type: "custom" }, "label"))[0]).toBe('& [data-type="custom"] > .fb-field > label');
    expect(selectorsFor(T({ kind: "in", key: "adr", sub: "ville" }, "label"))[0]).toBe('& [data-key="adr"] .ctl [data-key="ville"] > .fb-field > label');
    expect(selectorsFor(T({ kind: "key", key: "adr" }, "field"))).toEqual(['& [data-key="adr"] > .fb-field']);
  });
  it("sans portée, ou pour tout l'intérieur d'un contrôle, tous les libellés sont visés", () => {
    expect(selectorsFor(T({ kind: "all" }, "label"))[0]).toBe("& .fb-field > label");
    expect(selectorsFor(T({ kind: "in", key: "adr" }, "label"))[0]).toBe('& [data-key="adr"] .ctl .fb-field > label');
  });
  it("les parties sans version « propre » (saisie, options) gardent leur sélecteur large", () => {
    expect(selectorsFor(T({ kind: "key", key: "nom" }, "input"))[0]).toBe('& [data-key="nom"] input[type="text"]');
  });
  it("une règle écrite relit sa cible", () => {
    for (const t of [T({ kind: "key", key: "adr" }, "label"), T({ kind: "type", type: "custom" }, "help"), T({ kind: "in", key: "adr", sub: "v" }, "error")]) expect(targetOf(ruleOf(t))).toMatchObject({ part: t.part, scope: t.scope });
  });
});
