// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

// @ts-expect-error module JS de l'extension, sans déclaration de types
import mountUntyped from "../../../backend/apistore/ui/index.js";

type Mount = (el: HTMLElement, host: unknown) => () => void;
const mountStore = mountUntyped as Mount;
const PUBLISHED = {
  slug: "meteo",
  name: "Météo",
  description: "<img src=x onerror=alert(1)>",
  status: "published",
  workflows: ["forecast"],
  plans: [{ slug: "gratuit", name: "Gratuit", per_minute: 10, per_day: null, approval: "manual", environments: ["prod"] }],
};
const flush = async () => {
  for (let i = 0; i < 6; i++) await Promise.resolve();
};

async function open(handler: (path: string, init?: { method?: string; json?: unknown }) => unknown) {
  const api = vi.fn(async (p: string, init?: { method?: string; json?: unknown }) => handler(p, init));
  const el = document.createElement("div");
  document.body.append(el);
  const stop = mountStore(el, { apiVersion: 1, workspace: { slug: "team-a", name: "A" }, user: { username: "alice" }, path: "/", api, navigate: vi.fn() });
  await flush();
  return { el, api, stop };
}

const routes =
  (extra: Record<string, unknown> = {}) =>
  (p: string) => {
    if (p.endsWith("/products/")) return [PUBLISHED];
    if (p.endsWith("/subscriptions/")) return [];
    return extra[p] ?? {};
  };

describe("interface de l'API store", () => {
  beforeEach(() => {
    document.body.replaceChildren();
  });

  it("affiche le catalogue sans jamais interpréter le texte du serveur comme du HTML", async () => {
    const { el } = await open(routes());
    expect(el.textContent).toContain("Météo");
    expect(el.querySelector("img")).toBeNull();
    expect(el.textContent).toContain("<img src=x");
    expect(el.textContent).toContain("10 appels / minute");
    expect(el.textContent).toContain("Validation par un approbateur");
    expect(el.querySelector("input[type=radio]:checked")).not.toBeNull(); // un plan est présélectionné
  });

  it("n'affiche l'administration qu'aux comptes que le serveur déclare administrateurs", async () => {
    const lecteur = await open(routes());
    expect(lecteur.el.textContent).not.toContain("Administration");
    document.body.replaceChildren();
    const admin = await open((p) => (p.endsWith("/me/") ? { administer: true, approve: true, run: true } : []));
    expect(admin.el.textContent).toContain("Administration"); // même sans aucun produit : le premier reste à créer
  });

  it("demande une souscription au bon endroit", async () => {
    const { el, api } = await open(routes());
    (el.querySelector("input[placeholder='Application consommatrice']") as HTMLInputElement).value = "  app-1 ";
    el.querySelector("form")?.dispatchEvent(new Event("submit", { cancelable: true }));
    await flush();
    expect(api).toHaveBeenCalledWith("/plugins/apistore/manage/team-a/subscriptions/", { json: { product: "meteo", plan: "gratuit", consumer: "app-1" } });
  });

  it("montre la clé en clair une seule fois, puis plus jamais", async () => {
    const sub = {
      id: 7,
      product: "meteo",
      plan: "gratuit",
      consumer: "app",
      status: "active",
      requested_by: "alice",
      created_at: "2026-11-01T00:00:00Z",
      keys: [],
    };
    const { el } = await open((p) => {
      if (p.endsWith("/products/")) return [PUBLISHED];
      if (p.endsWith("/subscriptions/")) return [sub];
      if (p.endsWith("/keys/")) return { id: 1, prefix: "fxs_ab", expires_at: "2027-01-01T00:00:00Z", key: "fxs_SECRET" };
      return {};
    });
    [...el.querySelectorAll("button[role=tab]")][1]?.dispatchEvent(new Event("click"));
    (el.querySelector("button[aria-label^='Émettre']") as HTMLButtonElement).click();
    await flush();
    expect(el.querySelector("[data-testid=api-key]")?.textContent).toBe("fxs_SECRET");
    [...el.querySelectorAll("button[role=tab]")][0]?.dispatchEvent(new Event("click"));
    expect(el.textContent).not.toContain("fxs_SECRET");
  });

  it("n'écrit jamais « null » ni « undefined » dans la page", async () => {
    const { el } = await open(routes());
    expect(el.textContent).not.toMatch(/null|undefined/);
  });

  it("affiche le refus du serveur et nettoie à la fermeture", async () => {
    const { el, stop } = await open(() => {
      throw { message: "Accès refusé." };
    });
    expect(el.querySelector("[role=alert]")?.textContent).toBe("Accès refusé.");
    stop();
    expect(typeof stop).toBe("function");
  });

  it("liste les demandes en attente et envoie la décision", async () => {
    const sub = {
      id: 3,
      product: "meteo",
      plan: "gratuit",
      consumer: "app",
      status: "pending",
      requested_by: "bob",
      created_at: "2026-11-01T00:00:00Z",
      keys: [],
    };
    const { el, api } = await open((p) => (p.endsWith("/products/") ? [PUBLISHED] : p.endsWith("/subscriptions/") ? [sub] : {}));
    [...el.querySelectorAll("button[role=tab]")][2]?.dispatchEvent(new Event("click"));
    (el.querySelector("button[aria-label='Approuver la demande 3']") as HTMLButtonElement).click();
    await flush();
    expect(api).toHaveBeenCalledWith("/plugins/apistore/manage/team-a/subscriptions/3/decision/", { json: { approve: true } });
  });

  it("propose les workflows en cases à cocher et les comptes en liste, et envoie la sélection", async () => {
    const product = { ...PUBLISHED, status: "draft", run_as: "bot" };
    const { el, api } = await open((p) => {
      if (p.endsWith("/me/")) return { administer: true, approve: true, run: true };
      if (p.endsWith("/options/"))
        return {
          workflows: [
            { slug: "forecast", name: "Prévisions", published: true },
            { slug: "brouillon", name: "Brouillon", published: false },
          ],
          accounts: ["bot", "autre"],
        };
      return p.endsWith("/products/") ? [product] : [];
    });
    [...el.querySelectorAll("button[role=tab]")].at(-1)?.dispatchEvent(new Event("click"));
    const boxes = [...el.querySelectorAll<HTMLInputElement>("fieldset input[type=checkbox]")];
    expect(boxes.map((b) => [b.value, b.checked, b.disabled])).toEqual([
      ["forecast", true, false],
      ["brouillon", false, true],
    ]);
    const select = el.querySelector("select[aria-label^='Compte de service']") as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(["", "bot", "autre"]);
    expect(select.value).toBe("bot");
    select.value = "autre";
    const forms = [...el.querySelectorAll("form")];
    forms.find((f) => f.querySelector("fieldset"))?.dispatchEvent(new Event("submit", { cancelable: true }));
    await flush();
    expect(api).toHaveBeenCalledWith("/plugins/apistore/manage/team-a/products/meteo/", {
      method: "PATCH",
      json: { workflows: ["forecast"], run_as: "autre" },
    });
  });

  it("publier envoie aussi la sélection non enregistrée (workflows et compte)", async () => {
    const product = { ...PUBLISHED, status: "draft", workflows: [], run_as: null };
    const { el, api } = await open((p) => {
      if (p.endsWith("/me/")) return { administer: true, approve: true, run: true };
      if (p.endsWith("/options/")) return { workflows: [{ slug: "forecast", name: "Prévisions", published: true }], accounts: ["bot"] };
      return p.endsWith("/products/") ? [product] : [];
    });
    [...el.querySelectorAll("button[role=tab]")].at(-1)?.dispatchEvent(new Event("click"));
    (el.querySelector("fieldset input[type=checkbox]") as HTMLInputElement).checked = true;
    (el.querySelector("select[aria-label^='Compte de service']") as HTMLSelectElement).value = "bot";
    (el.querySelector("button[aria-label^='Publier']") as HTMLButtonElement).click();
    await flush();
    expect(api).toHaveBeenCalledWith("/plugins/apistore/manage/team-a/products/meteo/", {
      method: "PATCH",
      json: { workflows: ["forecast"], run_as: "bot", status: "published" },
    });
  });

  const SUB = { product: "meteo", plan: "gratuit", consumer: "app", requested_by: "alice", created_at: "2026-11-01T00:00:00Z", keys: [] };
  const ADMIN = { administer: true, approve: true, run: true };
  const archiveSetup = () =>
    open((p) => {
      if (p.endsWith("/me/")) return ADMIN;
      if (p.endsWith("/options/")) return { workflows: [], accounts: [] };
      if (p.endsWith("/products/")) return [PUBLISHED];
      if (p.endsWith("/subscriptions/"))
        return [
          { ...SUB, id: 1, status: "active" },
          { ...SUB, id: 2, status: "revoked" },
          { ...SUB, id: 3, status: "rejected" },
        ];
      return {};
    });
  const openSubs = (el: HTMLElement) => [...el.querySelectorAll("button[role=tab]")][1]?.dispatchEvent(new Event("click"));
  const subTab = (el: HTMLElement, name: RegExp) =>
    [...el.querySelectorAll("button[role=tab]")].find((b) => name.test(b.textContent ?? ""))?.dispatchEvent(new Event("click"));

  it("sépare les souscriptions actives des archivées (révoquées ou refusées)", async () => {
    const { el } = await archiveSetup();
    openSubs(el);
    expect(el.querySelectorAll("article")).toHaveLength(1);
    expect(el.textContent).toContain("Actives1");
    expect(el.textContent).toContain("Archivées2");
    subTab(el, /Archivées/);
    expect(el.querySelectorAll("article")).toHaveLength(2);
    expect(el.querySelector("button[aria-label='Réactiver la souscription 2']")).not.toBeNull();
    expect(el.querySelector("button[aria-label='Réactiver la souscription 3']")).toBeNull(); // une demande refusée ne se réactive pas
  });

  it("réactive depuis l'archive", async () => {
    const { el, api } = await archiveSetup();
    openSubs(el);
    subTab(el, /Archivées/);
    (el.querySelector("button[aria-label='Réactiver la souscription 2']") as HTMLButtonElement).click();
    await flush();
    expect(api).toHaveBeenCalledWith("/plugins/apistore/manage/team-a/subscriptions/2/status/", { json: { status: "active" } });
  });

  it("ne supprime qu'après confirmation", async () => {
    const { el, api } = await archiveSetup();
    openSubs(el);
    subTab(el, /Archivées/);
    (el.querySelector("button[aria-label='Supprimer la souscription 2']") as HTMLButtonElement).click();
    expect(api).not.toHaveBeenCalledWith(expect.stringContaining("/subscriptions/2/"), { method: "DELETE" });
    (el.querySelector("button[aria-label='Confirmer la suppression de 2']") as HTMLButtonElement).click();
    await flush();
    expect(api).toHaveBeenCalledWith("/plugins/apistore/manage/team-a/subscriptions/2/", { method: "DELETE" });
  });

  it("n'offre ni réactivation ni suppression à qui n'administre pas", async () => {
    const { el } = await open((p) => (p.endsWith("/subscriptions/") ? [{ ...SUB, id: 2, status: "revoked" }] : p.endsWith("/products/") ? [PUBLISHED] : {}));
    openSubs(el);
    subTab(el, /Archivées/);
    expect(el.querySelector("button[aria-label^='Réactiver'], button[aria-label^='Supprimer']")).toBeNull();
  });

  const many = Array.from({ length: 100 }, (_, i) => ({
    ...PUBLISHED,
    slug: `p${i}`,
    name: `Produit ${String(i).padStart(3, "0")}`,
    status: i % 2 ? "draft" : "published",
    workflows: i === 42 ? ["rare"] : ["forecast"],
    workflow_names: i === 42 ? { rare: "Workflow introuvable ailleurs" } : {},
  }));
  const adminMany = () =>
    open((p) => {
      if (p.endsWith("/me/")) return ADMIN;
      if (p.endsWith("/options/")) return { workflows: [], accounts: [] };
      if (p.endsWith("/products/")) return many;
      return [];
    });
  const goAdmin = (el: HTMLElement) => [...el.querySelectorAll("button[role=tab]")].at(-1)?.dispatchEvent(new Event("click"));
  const rows = (el: HTMLElement) => el.querySelectorAll("button.fx-row").length;

  it("administration : 100 produits en liste, filtrables par texte (nom, identifiant, workflow) et par état", async () => {
    const { el } = await adminMany();
    goAdmin(el);
    expect(rows(el)).toBe(100);
    expect(el.querySelector("[role=status]")?.textContent).toBe("100 produits sur 100");
    const search = el.querySelector("input[type=search]") as HTMLInputElement;
    search.value = "introuvable ailleurs";
    search.dispatchEvent(new Event("input"));
    expect(rows(el)).toBe(1);
    expect(el.querySelector("[role=status]")?.textContent).toBe("1 produit sur 100");
    search.value = "";
    search.dispatchEvent(new Event("input"));
    const state = el.querySelector("select[aria-label='Filtrer par état']") as HTMLSelectElement;
    state.value = "draft";
    state.dispatchEvent(new Event("change"));
    expect(rows(el)).toBe(50);
  });

  it("administration : choisir un produit dans la liste ouvre son éditeur", async () => {
    const { el } = await adminMany();
    goAdmin(el);
    expect(el.querySelector(".fx-main h3")?.textContent).toBe("Produit 000"); // le premier est ouvert d'office
    (el.querySelectorAll("button.fx-row")[7] as HTMLButtonElement).click();
    expect(el.querySelector(".fx-main h3")?.textContent).toBe("Produit 007");
    expect(el.querySelector("button.fx-row[aria-current=true]")?.textContent).toContain("Produit 007");
  });

  it("administration : « + Nouveau produit » ouvre le formulaire de création et envoie la description", async () => {
    const { el, api } = await adminMany();
    goAdmin(el);
    ([...el.querySelectorAll("aside button")].find((b) => /Nouveau produit/.test(b.textContent ?? "")) as HTMLButtonElement).click();
    (el.querySelector("input[aria-label='Identifiant du produit']") as HTMLInputElement).value = "neuf";
    (el.querySelector("input[aria-label='Nom du produit']") as HTMLInputElement).value = "Neuf";
    (el.querySelector("input[aria-label='Description du produit']") as HTMLInputElement).value = "Pour voir";
    el.querySelector(".fx-main form")?.dispatchEvent(new Event("submit", { cancelable: true }));
    await flush();
    expect(api).toHaveBeenCalledWith("/plugins/apistore/manage/team-a/products/", { json: { slug: "neuf", name: "Neuf", description: "Pour voir" } });
  });

  it("fiche produit : la recherche de workflows masque sans perdre la sélection, et compte les choisis", async () => {
    const wfs = Array.from({ length: 30 }, (_, i) => ({ slug: `wf-${i}`, name: i === 17 ? "Facturation mensuelle" : `Flux ${i}`, published: true }));
    const product = { ...PUBLISHED, status: "draft", workflows: ["wf-3"], run_as: "bot" };
    const { el, api } = await open((p) => {
      if (p.endsWith("/me/")) return ADMIN;
      if (p.endsWith("/options/")) return { workflows: wfs, accounts: ["bot"] };
      return p.endsWith("/products/") ? [product] : [];
    });
    [...el.querySelectorAll("button[role=tab]")].at(-1)?.dispatchEvent(new Event("click"));
    const visible = () => [...el.querySelectorAll<HTMLElement>("fieldset label.fx-pick")].filter((l) => !l.hidden).length;
    const status = () => el.querySelector("fieldset [role=status]")?.textContent;
    expect(visible()).toBe(30);
    expect(status()).toBe("1 sélectionné sur 30");
    const finder = el.querySelector("input[aria-label^='Rechercher un workflow']") as HTMLInputElement;
    finder.value = "factur";
    finder.dispatchEvent(new Event("input"));
    expect(visible()).toBe(1);
    (el.querySelector("fieldset label:not([hidden]) input") as HTMLInputElement).click(); // coche « Facturation mensuelle »
    expect(status()).toBe("2 sélectionnés sur 30");
    finder.value = "zzz";
    finder.dispatchEvent(new Event("input"));
    expect(visible()).toBe(0);
    expect(el.querySelector("fieldset")?.textContent).toContain("Aucun workflow ne correspond.");
    finder.value = "";
    finder.dispatchEvent(new Event("input"));
    expect(visible()).toBe(30);
    el.querySelector(".fx-main form")?.dispatchEvent(new Event("submit", { cancelable: true }));
    await flush();
    expect(api).toHaveBeenCalledWith("/plugins/apistore/manage/team-a/products/meteo/", {
      method: "PATCH",
      json: { workflows: ["wf-3", "wf-17"], run_as: "bot" },
    });
  });

  const keySetup = (rights: Record<string, boolean>) =>
    open((p, init) => {
      if (p.endsWith("/me/")) return rights;
      if (p.endsWith("/options/")) return { workflows: [], accounts: [] };
      if (p.endsWith("/products/")) return [PUBLISHED];
      if (p.endsWith("/subscriptions/")) return [{ ...SUB, id: 7, status: "active", keys: [{ id: 1, prefix: "fxs_ab", expires_at: null, revoked: false }] }];
      if (p.endsWith("/keys/"))
        return { id: 2, prefix: "fxs_cd", expires_at: init?.json && "unlimited" in (init.json as object) ? null : "2027-01-01T00:00:00Z", key: "fxs_X" };
      return {};
    });
  const choose = (el: HTMLElement, value: string) => {
    const select = el.querySelector("select[aria-label^='Durée de validité']") as HTMLSelectElement;
    select.value = value;
    select.dispatchEvent(new Event("change"));
  };
  const issue = async (el: HTMLElement) => {
    (el.querySelector("button[aria-label^='Émettre']") as HTMLButtonElement).click();
    await flush();
  };

  it("clé : la durée choisie est envoyée (90 jours par défaut, durée personnalisée possible)", async () => {
    const { el, api } = await keySetup({ administer: false, approve: false, run: true });
    openSubs(el);
    expect(el.textContent).toContain("sans expiration"); // une clé existante sans date est dite telle
    const select = el.querySelector("select[aria-label^='Durée de validité']") as HTMLSelectElement;
    expect(select.value).toBe("90");
    expect([...select.options].map((o) => o.value)).not.toContain("unlimited"); // réservé aux administrateurs
    await issue(el);
    expect(api).toHaveBeenCalledWith("/plugins/apistore/manage/team-a/subscriptions/7/keys/", { json: { days: 90 } });
    openSubs(el);
    choose(el, "custom");
    const days = el.querySelector("input[aria-label^='Nombre de jours']") as HTMLInputElement;
    expect(days.hidden).toBe(false);
    days.value = "12";
    await issue(el);
    expect(api).toHaveBeenCalledWith("/plugins/apistore/manage/team-a/subscriptions/7/keys/", { json: { days: 12 } });
  });

  it("clé : « Illimitée » n'existe que pour un administrateur et l'annonce clairement", async () => {
    const { el, api } = await keySetup({ administer: true, approve: true, run: true });
    openSubs(el);
    choose(el, "unlimited");
    await issue(el);
    expect(api).toHaveBeenCalledWith("/plugins/apistore/manage/team-a/subscriptions/7/keys/", { json: { unlimited: true } });
    expect(el.querySelector("[role=status]")?.textContent).toContain("sans expiration");
  });
});
