import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, login, publishViaApi } from "./helpers";

/** Rôle « Catalogue » : ne voit que le catalogue et ses propres exécutions, ne lance que ce que ses groupes autorisent. */

async function loginAs(page: Page, user: string) {
  await page.goto("/");
  await page.getByLabel("Identifiant").fill(user);
  await page.getByLabel("Mot de passe").fill("demo-fluxonata");
  await page.getByRole("button", { name: "Se connecter" }).click();
  await expect(page.getByRole("navigation", { name: "Navigation principale" })).toBeVisible({ timeout: 20_000 });
}
async function api(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ({ method, path, body }) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    const r = await fetch(`/api/v1/workspaces/demo${path}`, { method, headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: body ? JSON.stringify(body) : undefined });
    return { status: r.status, data: await r.json().catch(() => ({})) };
  }, { method, path, body });
}
const graph = {
  inputs: { type: "object", properties: { sujet: { type: "string", title: "Sujet" }, mdp: { type: "string", title: "Mot de passe" } }, required: [] },
  outputs: {}, edges: [{ from: "a", to: "b" }],
  nodes: [{ id: "a", type: "wait", name: "Pause", seconds: 3 }, { id: "b", type: "wait", name: "Seconde pause", seconds: 1 }],
};

test("rôle Catalogue : groupes, catalogue restreint, exécutions personnelles, relance pré-remplie", async ({ page, browser }) => {
  await login(page, "admin");
  const id = Date.now().toString().slice(-6);
  const ouvert = `cat-ouvert-${id}`, ferme = `cat-ferme-${id}`;
  for (const slug of [ouvert, ferme]) {
    expect(await publishViaApi(page, slug, graph)).toBe(201);
    expect((await api(page, "POST", `/workflows/${slug}/catalog/`)).status).toBe(200);
  }
  // l'administrateur crée un groupe avec dave et une seule automatisation
  await page.goto("/admin");
  await page.getByRole("tab", { name: "Groupes" }).click();
  await page.getByRole("button", { name: "Nouveau groupe" }).click();
  const dlg = page.getByRole("dialog", { name: "Nouveau groupe" });
  await dlg.getByLabel("Nom du groupe").fill(`Compta ${id}`);
  await dlg.getByRole("group", { name: "Membres du groupe" }).getByRole("checkbox", { name: /dave/ }).check();
  await dlg.getByRole("group", { name: /Automatisations/ }).getByRole("checkbox", { name: `Test ${ouvert}` }).check();
  await dlg.getByRole("button", { name: "Enregistrer" }).click();
  await expect(page.getByRole("row", { name: new RegExp(`Compta ${id}`) })).toContainText("dave");

  // dave : un menu réduit, un catalogue réduit
  const dave = await (await browser.newContext()).newPage();
  await loginAs(dave, "dave");
  const nav = dave.getByRole("navigation", { name: "Navigation principale" });
  await expect(nav.getByRole("link")).toHaveText(["Catalogue", "Mes exécutions"]);
  await expect(dave.getByRole("heading", { name: "Catalogue d'automatisations" })).toBeVisible();
  await expect(dave.getByText(`Test ${ouvert}`).first()).toBeVisible();
  await expect(dave.getByText(`Test ${ferme}`)).toHaveCount(0);
  await dave.goto("/workflows");
  await expect(dave).toHaveURL(/\/catalog$/); // aucune autre page
  expect((await api(dave, "GET", "/workflows/")).status).toBe(403);
  expect((await api(dave, "GET", `/catalog/${ferme}/`)).status).toBe(404);

  // lancement (dave peut déjà avoir des exécutions d'un essai précédent)
  const before = (await api(dave, "GET", "/catalog-runs/")).data.results.length as number;
  await dave.goto(`/catalog/${ouvert}`);
  await dave.getByLabel("Sujet").fill("Première demande");
  await dave.getByLabel("Mot de passe").fill("s3cret-pw");
  await expectNoA11yViolations(dave);
  await dave.getByRole("button", { name: "Lancer" }).click();
  await expect(dave).toHaveURL(/\/executions\/[0-9a-f-]{36}$/);
  await expect(dave.getByRole("heading", { level: 1, name: `Test ${ouvert}` })).toBeVisible();
  // données saisies : le mot de passe est masqué ; le retour du workflow est présent
  const saisie = dave.getByRole("region", { name: "Données saisies" });
  await expect(saisie).toContainText("Première demande");
  await expect(saisie).toContainText("••••••••");
  await expect(dave.locator("body")).not.toContainText("s3cret-pw");
  await expect(dave.getByRole("heading", { name: "Retour du workflow" })).toBeVisible();
  await expect(dave.getByText("Réussie").or(dave.getByText("Terminée")).first()).toBeVisible({ timeout: 40_000 });
  await expectNoA11yViolations(dave);

  // relance : formulaire pré-rempli (sauf le secret), modifié puis relancé
  await dave.getByRole("button", { name: "Relancer avec ces données" }).click();
  await expect(dave).toHaveURL(new RegExp(`/catalog/${ouvert}$`));
  await expect(dave.getByLabel("Sujet")).toHaveValue("Première demande");
  await expect(dave.getByLabel("Mot de passe")).toHaveValue("");
  await dave.getByLabel("Sujet").fill("Seconde demande");
  await dave.getByLabel("Mot de passe").fill("autre-pw");
  await dave.getByRole("button", { name: "Lancer" }).click();
  await expect(dave).toHaveURL(/\/executions\/[0-9a-f-]{36}$/);

  // « Mes exécutions » : seulement les siennes, avec une barre de progression
  await nav.getByRole("link", { name: "Mes exécutions" }).click();
  const table = dave.getByRole("table", { name: "Mes exécutions" });
  await expect(table.getByRole("row")).toHaveCount(before + 3); // en-tête + les anciens + 2 lancements
  await expect(table.getByRole("progressbar").first()).toBeVisible();
  await expectNoA11yViolations(dave);
  // l'administrateur, lui, ne voit pas ces exécutions dans la page de dave (et dave n'a pas accès aux autres)
  const list = await api(dave, "GET", "/catalog-runs/");
  expect(list.data.results).toHaveLength(before + 2);
  expect((await api(dave, "GET", "/workflow-runs/")).status).toBe(403);
});

test("déconnexion : la page de connexion s'affiche aussitôt, sans recharger, et on peut se reconnecter", async ({ page }) => {
  await login(page, "alice");
  await page.getByRole("button", { name: "Se déconnecter" }).click();
  await expect(page.getByRole("button", { name: "Se connecter" })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByLabel("Identifiant")).toBeVisible();
  await page.getByLabel("Identifiant").fill("bob");
  await page.getByLabel("Mot de passe").fill("demo-fluxonata");
  await page.getByRole("button", { name: "Se connecter" }).click();
  await expect(page.getByRole("navigation", { name: "Navigation principale" })).toBeVisible({ timeout: 20_000 });
});
