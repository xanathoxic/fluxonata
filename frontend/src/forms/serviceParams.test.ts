import { describe, expect, it } from "vitest";
import { choicesToText, connectionLabel, textToChoices, waitingFor, withChooser } from "./serviceParams";
import type { DataSource } from "./types";

const src = (patch: Partial<DataSource>): DataSource => ({ connection: "svc", path: "/", ...patch });

describe("waitingFor : les champs qu'une liste dynamique attend avant d'interroger le service", () => {
  it("attend chaque {champ} du chemin avant le « ? » tant qu'il est vide", () => {
    const s = src({ path: "/acl/{champ1}/org/{champ2}", params: ["champ1", "champ2"] });
    expect(waitingFor(s, {})).toEqual(["champ1", "champ2"]);
    expect(waitingFor(s, { champ1: "prod" })).toEqual(["champ2"]);
    expect(waitingFor(s, { champ1: "prod", champ2: "web" })).toEqual([]);
    expect(waitingFor(s, { champ1: "prod", champ2: "" })).toEqual(["champ2"]);
    expect(waitingFor(s, { champ1: "prod", champ2: [] })).toEqual(["champ2"]); // une liste cochée vide est vide
  });
  it("une valeur 0 ou false est une valeur", () => {
    expect(waitingFor(src({ path: "/x/{n}", params: ["n"] }), { n: 0 })).toEqual([]);
    expect(waitingFor(src({ path: "/x/{n}", params: ["n"] }), { n: false })).toEqual([]);
  });
  it("n'attend pas un {champ} placé après le « ? » (peut être vide : « tous ») ni {q} (la zone de recherche)", () => {
    expect(waitingFor(src({ path: "/modeles?marque={marque}", params: ["marque"] }), {})).toEqual([]);
    expect(waitingFor(src({ path: "/search/{q}", params: ["q"] }), {})).toEqual([]);
    expect(waitingFor(src({ path: "/marques?q={q}", params: ["q"] }), {})).toEqual([]);
  });
  it("attend aussi les {champ} du chemin où la liste est lue, une seule fois chacun", () => {
    expect(waitingFor(src({ path: "/acl/{groupe}", items_path: "Groups.{groupe}", params: ["groupe"] }), {})).toEqual(["groupe"]);
    expect(waitingFor(src({ path: "/acl", items_path: "Groups.{groupe}", params: ["groupe"] }), {})).toEqual(["groupe"]);
  });
  it("ignore un {champ} non déclaré dans les champs lus (le serveur le refusera) et l'absence de source", () => {
    expect(waitingFor(src({ path: "/acl/{inconnu}", params: [] }), {})).toEqual([]);
    expect(waitingFor(undefined, {})).toEqual([]);
  });
});

describe("connexion choisie par un champ", () => {
  const s = src({ path: "/acl", params: ["env"], connection_from: "env", connections: { prod: "a", test: "b" } });
  it("attend la valeur du champ qui choisit la connexion : sans elle, aucune connexion", () => {
    expect(waitingFor(s, {})).toEqual(["env"]);
    expect(waitingFor(s, { env: "prod" })).toEqual([]);
  });
  it("résume la connexion fixe ou le champ qui choisit", () => {
    expect(connectionLabel({ connection: "svc" })).toBe("svc");
    expect(connectionLabel({ connection: "" })).toBe("(connexion ?)");
    expect(connectionLabel({ connection: "", connection_from: "env" })).toBe("connexion selon « env »");
  });
  it("lit et écrit la liste « valeur | connexion », une ligne par choix, 10 au plus, lignes incomplètes ignorées", () => {
    expect(textToChoices("prod | tailnet-prod\n test|tailnet-test \nsansbarre\n | vide\nseul |")).toEqual({ prod: "tailnet-prod", test: "tailnet-test" });
    expect(choicesToText({ prod: "a", test: "b" })).toBe("prod | a\ntest | b");
    expect(Object.keys(textToChoices(Array.from({ length: 15 }, (_, i) => `v${i} | c${i}`).join("\n")))).toHaveLength(10);
  });
  it("ajoute le champ qui choisit aux champs lus, une seule fois, 5 au plus", () => {
    expect(withChooser(src({ connection_from: "env", params: ["a"] })).params).toEqual(["a", "env"]);
    const done = src({ connection_from: "env", params: ["env"] });
    expect(withChooser(done)).toBe(done); // déjà présent : inchangé
    const plain = src({ params: ["a"] });
    expect(withChooser(plain)).toBe(plain); // pas de choix : inchangé
    expect(withChooser(src({ connection_from: "env", params: ["a", "b", "c", "d", "e"] })).params).toHaveLength(5);
  });
});
