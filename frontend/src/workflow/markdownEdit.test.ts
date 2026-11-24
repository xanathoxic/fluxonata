import { describe, expect, it } from "vitest";
import { applyFormat, imageMarkdown, insertText, TABLE_TEMPLATE, variableMarkdown } from "./markdownEdit";

const sel = (e: { value: string; start: number; end: number }) => e.value.slice(e.start, e.end);

describe("mise en forme Markdown", () => {
  it("met la sélection en gras, italique, barré ou code, et la retire au second clic", () => {
    const bold = applyFormat("un mot ici", 3, 6, "bold");
    expect(bold.value).toBe("un **mot** ici");
    expect(sel(bold)).toBe("mot");
    const back = applyFormat(bold.value, 3, 10, "bold");
    expect(back.value).toBe("un mot ici");
    expect(applyFormat("a b", 0, 1, "italic").value).toBe("*a* b");
    expect(applyFormat("a b", 2, 3, "strike").value).toBe("a ~~b~~");
    expect(applyFormat("a b", 0, 1, "code").value).toBe("`a` b");
  });

  it("retire aussi les marqueurs quand ils entourent la sélection", () => {
    expect(applyFormat("un **mot** ici", 5, 8, "bold").value).toBe("un mot ici");
  });

  it("sans sélection, insère un texte d'exemple sélectionné", () => {
    const e = applyFormat("", 0, 0, "bold");
    expect(e.value).toBe("**texte en gras**");
    expect(sel(e)).toBe("texte en gras");
  });

  it("fait un lien en sélectionnant l'adresse à compléter", () => {
    const e = applyFormat("voir ici", 5, 8, "link");
    expect(e.value).toBe("voir [ici](https://)");
    expect(sel(e)).toBe("https://");
  });

  it("préfixe chaque ligne choisie pour les titres, listes et citations, et remplace un titre existant", () => {
    expect(applyFormat("un\ndeux\ntrois", 0, 7, "ul").value).toBe("- un\n- deux\ntrois");
    expect(applyFormat("un\ndeux", 0, 7, "ol").value).toBe("1. un\n2. deux");
    expect(applyFormat("texte", 2, 2, "h2").value).toBe("## texte");
    expect(applyFormat("# titre", 3, 3, "h3").value).toBe("### titre");
    expect(applyFormat("a\nb", 0, 3, "quote").value).toBe("> a\n> b");
  });

  it("retire le préfixe quand toutes les lignes l'ont déjà", () => {
    expect(applyFormat("- un\n- deux", 0, 11, "ul").value).toBe("un\ndeux");
    expect(applyFormat("## t", 0, 0, "h2").value).toBe("t");
  });

  it("insère un tableau et un filet comme blocs séparés par des lignes vides", () => {
    const t = applyFormat("avant\napres", 5, 5, "table");
    expect(t.value).toBe(`avant\n\n${TABLE_TEMPLATE}\n\napres`);
    expect(applyFormat("", 0, 0, "rule").value).toBe("---");
    expect(applyFormat("a", 1, 1, "rule").value).toBe("a\n\n---");
  });

  it("insère une image ou une variable à la place de la sélection", () => {
    const img = imageMarkdown("123", "mon [logo]");
    expect(img).toBe("![mon logo](image:123)");
    expect(insertText("ab", 1, 1, variableMarkdown(" inputs.nom ")).value).toBe("a{{ inputs.nom }}b");
    expect(imageMarkdown("1", "")).toBe("![image](image:1)");
  });
});
