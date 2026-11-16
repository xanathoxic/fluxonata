import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown, parseBlocks } from "./Markdown";

const html = (source: string) => render(<Markdown source={source} />).container.innerHTML;

describe("Markdown", () => {
  it("met en forme titres, gras, italique, code, listes, citations et séparateurs", () => {
    const { container } = render(
      <Markdown
        source={"# Titre\n\nUn **gras**, un *italique* et du `code`.\n\n- un\n- deux\n\n1. premier\n2. second\n\n> cité\n\n---\n\n```\nligne 1\nligne 2\n```"}
      />,
    );
    expect(container.querySelector("h3")?.textContent).toBe("Titre");
    expect(container.querySelector("strong")?.textContent).toBe("gras");
    expect(container.querySelector("em")?.textContent).toBe("italique");
    expect(container.querySelector("p code")?.textContent).toBe("code");
    expect([...container.querySelectorAll("ul li")].map((l) => l.textContent)).toEqual(["un", "deux"]);
    expect([...container.querySelectorAll("ol li")].map((l) => l.textContent)).toEqual(["premier", "second"]);
    expect(container.querySelector("blockquote")?.textContent).toBe("cité");
    expect(container.querySelector("hr")).not.toBeNull();
    expect(container.querySelector("pre code")?.textContent).toBe("ligne 1\nligne 2");
  });

  it("démarre les titres à h3 (le titre de la fenêtre est le h2) et plafonne à h6", () => {
    const c = render(<Markdown source={"# a\n\n## b\n\n### c\n\n#### d"} />).container;
    expect([...c.querySelectorAll("h3,h4,h5,h6")].map((h) => h.tagName)).toEqual(["H3", "H4", "H5", "H6"]);
  });

  it("joint les lignes d'un paragraphe et sépare les paragraphes par une ligne vide", () => {
    expect(parseBlocks("a\nb\n\nc")).toEqual([
      { kind: "paragraph", text: "a b" },
      { kind: "paragraph", text: "c" },
    ]);
  });

  it("n'écrit jamais de HTML : une balise écrite par l'auteur reste du texte", () => {
    const out = html("<script>alert(1)</script> et <img src=x onerror=alert(1)>");
    expect(out).not.toContain("<script");
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;script&gt;");
  });

  it("n'accepte comme liens que http, https et mailto", () => {
    const c = render(
      <Markdown source={"[ok](https://exemple.test/x) [mail](mailto:a@b.test) [mal](javascript:alert(1)) [data](data:text/html,x)"} />,
    ).container;
    const links = [...c.querySelectorAll("a")];
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["https://exemple.test/x", "mailto:a@b.test"]);
    expect(links.every((a) => a.getAttribute("rel") === "noopener noreferrer" && a.getAttribute("target") === "_blank")).toBe(true);
    expect(c.textContent).toContain("mal");
    expect(c.innerHTML).not.toContain("javascript:");
  });

  it("garde le gras dans l'italique et l'inverse, et laisse intact un texte sans mise en forme", () => {
    expect(html("**a _b_**")).toContain("<strong>a <em>b</em></strong>");
    expect(html("simple 2 * 3 = 6")).toBe('<div class="md"><p>simple 2 * 3 = 6</p></div>');
  });

  it("ne rend rien pour un texte vide", () => {
    expect(html("")).toBe('<div class="md"></div>');
  });
});
