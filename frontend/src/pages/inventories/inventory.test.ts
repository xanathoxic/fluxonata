import { describe, expect, it } from "vitest";
import { docProblem, loopAround, parseVars, removeGroup, renameGroup, toggleIn, varsText, type InvDoc } from "./inventory";

const doc = (over: Partial<InvDoc> = {}): InvDoc => ({
  slug: "prod",
  name: "Production",
  description: "",
  variables: {},
  groups: [
    { name: "web", variables: {}, children: [] },
    { name: "db", variables: {}, children: [] },
    { name: "france", variables: {}, children: ["web", "db"] },
  ],
  hosts: [
    { name: "web1", variables: {}, enabled: true, groups: ["web", "france"] },
    { name: "db1", variables: {}, enabled: true, groups: ["db"] },
  ],
  ...over,
});

describe("variables JSON", () => {
  it("accepte un objet, ou rien", () => {
    expect(parseVars('{"ansible_user":"deploy"}')).toEqual({ ok: true, value: { ansible_user: "deploy" } });
    expect(parseVars("   ")).toEqual({ ok: true, value: {} });
  });
  it.each(["[1]", "3", '"x"', "null", "{oups", ""])("refuse %s quand ce n'est pas un objet lisible", (t) => {
    const r = parseVars(t);
    if (t === "") expect(r.ok).toBe(true);
    else expect(r.ok).toBe(false);
  });
  it("s'écrit sur une ligne, ou reste vide", () => {
    expect(varsText({ a: 1 })).toBe('{"a":1}');
    expect(varsText({})).toBe("");
  });
});

describe("ce qui empêche d'enregistrer", () => {
  it("rien sur un inventaire sain", () => expect(docProblem(doc())).toBeNull());
  it("un nom est obligatoire", () => expect(docProblem(doc({ name: "  " }))).toMatch(/nom/));
  it.each(["web-1", "a b", "", "1abc", "all", "ungrouped"])("refuse le groupe « %s »", (name) => {
    expect(docProblem(doc({ groups: [{ name, variables: {}, children: [] }] }))).not.toBeNull();
  });
  it("refuse un groupe en double", () =>
    expect(
      docProblem(
        doc({
          groups: [
            { name: "a", variables: {}, children: [] },
            { name: "a", variables: {}, children: [] },
          ],
        }),
      ),
    ).toMatch(/deux fois/));
  it.each(["-x", "", "h; rm", "h h"])("refuse la machine « %s »", (name) => {
    expect(docProblem(doc({ hosts: [{ name, variables: {}, enabled: true, groups: [] }] }))).not.toBeNull();
  });
  it("accepte une adresse ou un nom pointé", () =>
    expect(
      docProblem(
        doc({
          hosts: [
            { name: "10.0.0.5", variables: {}, enabled: true, groups: [] },
            { name: "web1.exemple.fr", variables: {}, enabled: true, groups: [] },
          ],
        }),
      ),
    ).toBeNull());
  it("refuse une machine en double", () =>
    expect(
      docProblem(
        doc({
          hosts: [
            { name: "h", variables: {}, enabled: true, groups: [] },
            { name: "h", variables: {}, enabled: true, groups: [] },
          ],
        }),
      ),
    ).toMatch(/deux fois/));
});

describe("boucles entre groupes", () => {
  it("détecte un groupe qui se contient de proche en proche", () => {
    const g = [
      { name: "a", variables: {}, children: ["b"] },
      { name: "b", variables: {}, children: ["c"] },
      { name: "c", variables: {}, children: ["a"] },
    ];
    expect(loopAround(g)).toMatch(/boucle/);
    expect(docProblem(doc({ groups: g, hosts: [] }))).toMatch(/boucle/);
  });
  it("accepte une hiérarchie, même avec un sous-groupe partagé", () => {
    const g = [
      { name: "a", variables: {}, children: ["c"] },
      { name: "b", variables: {}, children: ["c"] },
      { name: "c", variables: {}, children: [] },
    ];
    expect(loopAround(g)).toBeNull();
  });
});

describe("renommer et supprimer un groupe", () => {
  it("le renommage suit chez les parents et dans les machines", () => {
    const d = renameGroup(doc(), "web", "frontaux");
    expect(d.groups.map((g) => g.name)).toEqual(["frontaux", "db", "france"]);
    expect(d.groups[2]?.children).toEqual(["frontaux", "db"]);
    expect(d.hosts[0]?.groups).toEqual(["frontaux", "france"]);
  });
  it("la suppression retire le groupe des parents et des machines, pas les machines", () => {
    const d = removeGroup(doc(), "web");
    expect(d.groups.map((g) => g.name)).toEqual(["db", "france"]);
    expect(d.groups[1]?.children).toEqual(["db"]);
    expect(d.hosts).toHaveLength(2);
    expect(d.hosts[0]?.groups).toEqual(["france"]);
  });
  it("toggleIn ajoute sans doublon et retire", () => {
    expect(toggleIn(["a"], "b", true)).toEqual(["a", "b"]);
    expect(toggleIn(["a", "b"], "b", true)).toEqual(["a", "b"]);
    expect(toggleIn(["a", "b"], "a", false)).toEqual(["b"]);
  });
});
