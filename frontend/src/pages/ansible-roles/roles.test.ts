import { describe, expect, it } from "vitest";
import { contractAdvice, exampleFor, filesProblem, grouped, isSemver, nextVersion, normContract, pathProblem, sameContract, sameFiles, starter } from "./roles";

describe("chemins de fichiers", () => {
  it.each([
    "tasks/main.yml",
    "tasks/sub/install.yaml",
    "handlers/main.yml",
    "defaults/main.yml",
    "vars/os/debian.yml",
    "meta/main.yml",
    "templates/page.j2",
    "templates/conf/nginx.conf.j2",
    "files/cle.txt",
  ])("accepte %s", (p) => {
    expect(pathProblem(p)).toBeNull();
  });
  it.each([
    ["", /invalide/],
    ["/etc/passwd", /invalide/],
    ["tasks\\main.yml", /invalide/],
    ["main.yml", /dossier/],
    ["../x.yml", /Lettres|dossier/],
    ["tasks/../x.yml", /Lettres/],
    ["tasks//x.yml", /Lettres/],
    ["tasks/.cache.yml", /Lettres/],
    ["library/mod.py", /n'existe pas/],
    ["plugins/x.yml", /n'existe pas/],
    ["tasks/main.txt", /YAML/],
    ["defaults/main.py", /YAML/],
    ["a/b/c/d/e/f/g.yml", /Lettres|n'existe pas/],
  ])("refuse %j", (p, why) => {
    expect(pathProblem(p)).toMatch(why);
  });
  it("les modèles et fichiers libres n'exigent pas d'extension YAML", () => {
    expect(pathProblem("templates/page.txt")).toBeNull();
    expect(pathProblem("files/binaire.dat")).toBeNull();
  });
});

describe("ce qui empêche de publier", () => {
  it("exige tasks/main.yml", () => {
    expect(filesProblem({ "defaults/main.yml": "a: 1\n" })).toMatch(/tasks\/main.yml/);
    expect(filesProblem({})).toMatch(/tasks\/main.yml/);
    expect(filesProblem({ "tasks/main.yaml": "[]" })).toBeNull();
  });
  it("nomme le premier fichier invalide", () => {
    expect(filesProblem({ "tasks/main.yml": "[]", "library/x.py": "print(1)" })).toMatch(/^library\/x.py : /);
  });
});

describe("affichage et versions", () => {
  it("range les fichiers par dossier, tasks d'abord", () => {
    const g = grouped({ "templates/p.j2": "", "meta/main.yml": "", "tasks/b.yml": "", "tasks/main.yml": "", "defaults/main.yml": "" });
    expect(g.map((x) => x.dir)).toEqual(["tasks", "defaults", "meta", "templates"]);
    expect(g[0]?.paths).toEqual(["tasks/b.yml", "tasks/main.yml"]);
  });
  it("propose le numéro de correctif suivant", () => {
    expect(nextVersion(null)).toBe("1.0.0");
    expect(nextVersion("1.2.9")).toBe("1.2.10");
    expect(nextVersion("0.0.0")).toBe("0.0.1");
  });
  it("reconnaît un numéro de version", () => {
    expect(["1.0.0", "0.10.2"].every(isSemver)).toBe(true);
    expect(["1.0", "v1.0.0", "01.0.0", "1.0.0-rc1", ""].some(isSemver)).toBe(false);
  });
  it("compare deux ensembles de fichiers sans tenir compte de l'ordre", () => {
    expect(sameFiles({ a: "1", b: "2" }, { b: "2", a: "1" })).toBe(true);
    expect(sameFiles({ a: "1" }, { a: "2" })).toBe(false);
    expect(sameFiles({ a: "1" }, { a: "1", b: "" })).toBe(false);
  });
  it("propose un contenu de départ valable par dossier", () => {
    expect(starter("tasks/x.yml")).toContain("ansible.builtin.debug");
    expect(starter("templates/x.j2")).toBe("");
    expect(starter("meta/main.yml")).toBe("dependencies: []\n");
  });
});

describe("contrat d'usage d'un rôle", () => {
  const complet = { summary: "Installe le serveur web.", variables: [{ name: "port", type: "int", required: true, default: 80, description: "Le port." }] };
  it("un contrat absent et un contrat vide sont le même contrat", () => {
    expect(sameContract(undefined, {})).toBe(true);
    expect(sameContract({ summary: " " }, { summary: "" })).toBe(true);
    expect(normContract(undefined).variables).toEqual([]);
  });
  it("détecte une vraie modification (texte, variable, résultat)", () => {
    expect(sameContract(complet, { ...complet, summary: "Autre." })).toBe(false);
    expect(sameContract(complet, { ...complet, variables: [{ ...(complet.variables[0] as object), description: "Autre" }] as never })).toBe(false);
    expect(sameContract(complet, { ...complet, outputs: [{ name: "x", description: "" }] })).toBe(false);
  });
  it("dit ce qu'il manque : la phrase d'usage puis les variables sans description", () => {
    expect(contractAdvice(undefined)).toEqual(["Dites en une phrase à quoi sert le rôle."]);
    expect(contractAdvice(complet)).toEqual([]);
    expect(contractAdvice({ summary: "ok", variables: [{ name: "a", type: "str", required: false, description: "" }] })).toEqual([
      "Décrivez la variable « a ».",
    ]);
  });
  it("génère un exemple d'appel avec les variables obligatoires ou à valeur par défaut", () => {
    expect(exampleFor("web", complet)).toBe("- hosts: all\n  roles:\n    - role: web\n      vars:\n        port: 80\n");
    expect(exampleFor("web", { variables: [{ name: "cle", type: "str", required: true, description: "" }] })).toContain("cle: # à renseigner");
    expect(exampleFor("web", undefined)).toBe("- hosts: all\n  roles:\n    - role: web\n");
  });
});
