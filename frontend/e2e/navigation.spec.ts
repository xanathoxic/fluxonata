import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, login, publishViaApi } from "./helpers";

test("menu par intention et recherche globale Ctrl+K", async ({ page }) => {
  await login(page, "alice");
  const nav = page.getByRole("navigation", { name: "Navigation principale" });
  for (const g of ["Utiliser", "Concevoir", "Intégrer"]) await expect(nav.getByText(g, { exact: true })).toBeVisible();
  await expect(nav.getByText("Superviser", { exact: true })).toBeVisible(); // Exploitation est toujours proposée
  // alice n'administre pas : l'entrée Administration n'est pas dans le menu
  await expect(nav.getByRole("link", { name: "Administration" })).toHaveCount(0);
  await expectNoA11yViolations(page);

  const slug = `nav-${Date.now().toString().slice(-6)}`;
  expect(await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [], nodes: [{ id: "a", type: "wait", name: "Pause", seconds: 1 }] })).toBe(201);
  await page.goto("/home");
  await expect(page.getByRole("heading", { name: /Bonjour/ })).toBeVisible();
  await page.keyboard.press("Control+k");
  const dlg = page.getByRole("dialog", { name: "Recherche" });
  await expect(dlg).toBeVisible();
  await dlg.getByLabel("Rechercher dans l'espace").fill(slug);
  await expect(dlg.getByRole("region", { name: "Workflows" }).getByRole("button", { name: new RegExp(slug) })).toBeVisible();
  await expectNoA11yViolations(page);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/workflows/${slug}`));
  // dans l'éditeur de schéma, Ctrl+K garde « Aller à une étape »
  await page.goto(`/workflows/${slug}/schema`);
  await expect(page.getByRole("toolbar", { name: "Outils du schéma" })).toBeVisible();
  await page.keyboard.press("Control+k");
  await expect(page.getByRole("dialog", { name: "Aller à une étape" })).toBeVisible();
  await expect(page.getByRole("dialog", { name: "Recherche" })).toHaveCount(0);
});

test("les dates se lisent « 3 nov. 2026, 16:16 »", async ({ page }) => {
  await login(page, "alice");
  await page.goto("/workflows");
  await expect(page.locator("body")).not.toContainText(/\d{2}\/\d{2}\/\d{4}/);
  await expect(page.getByRole("table").first()).toContainText(/\d{1,2} \p{L}+\.? \d{4}/u);
});

test("accueil : exécutions récentes avant les raccourcis, guide repliable puis masquable", async ({ page }) => {
  await login(page, "alice");
  await page.evaluate(() => localStorage.removeItem("fluxonata.home.tutorial"));
  await page.goto("/home");
  const y = async (name: string) => (await page.getByRole("heading", { name }).boundingBox())!.y;
  await expect(page.getByRole("heading", { name: "Exécutions récentes" })).toBeVisible();
  expect(await y("Exécutions récentes")).toBeLessThan(await y("Démarrer rapidement"));
  const guide = page.getByRole("region", { name: "Démarrer en 5 minutes" });
  await expect(guide.getByRole("listitem")).toHaveCount(3);
  await guide.getByRole("button", { name: "Replier" }).click();
  await expect(guide.getByRole("listitem")).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("region", { name: "Démarrer en 5 minutes" }).getByRole("button", { name: "Déplier" })).toBeVisible(); // mémorisé
  await page.getByRole("region", { name: "Démarrer en 5 minutes" }).getByRole("button", { name: "Masquer" }).click();
  await expect(page.getByRole("heading", { name: "Démarrer en 5 minutes" })).toHaveCount(0);
  await page.getByRole("button", { name: /Afficher le guide/ }).click();
  await page.evaluate(() => localStorage.removeItem("fluxonata.home.tutorial"));
});

test("catalogue : cartes de même hauteur, « champs à renseigner », date relative", async ({ page }) => {
  await login(page, "alice");
  await page.goto("/catalog");
  const cards = page.locator(".cat-card");
  await expect(cards.first()).toBeVisible();
  const heights = await cards.evaluateAll((els) => els.slice(0, 6).map((e) => Math.round(e.getBoundingClientRect().height)));
  expect(new Set(heights.filter((_, i) => i < 3)).size).toBe(1); // une même rangée : une même hauteur
  await expect(cards.first()).toContainText(/(\d+ champs? à renseigner|aucun champ à renseigner)/);
  await expect(cards.first().locator("time")).toHaveText(/il y a|hier|aujourd|ce|dans/);
});

test("éditeur de formulaire : l'aperçu se règle en largeur (bureau, tablette, mobile)", async ({ page }) => {
  await login(page, "alice");
  const slug = `nav-form-${Date.now().toString().slice(-6)}`;
  await page.evaluate(async (slug) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    await fetch("/api/v1/workspaces/demo/form-library/", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify({ slug, name: `Largeur ${slug}`, definition: { version: 1, sections: [{ id: "s1", title: "S", fields: [{ key: "a", label: "A", type: "text" }] }] } }) });
  }, slug);
  await page.goto(`/library/forms/${slug}/design`);
  await page.getByRole("tablist", { name: "Vue de l'éditeur" }).getByRole("tab", { name: "Aperçu" }).click();
  const frame = page.locator(".fb-previewframe");
  const full = (await frame.boundingBox())!.width;
  await page.getByRole("tablist", { name: "Largeur de l'aperçu" }).getByRole("tab", { name: /Mobile/ }).click();
  expect((await frame.boundingBox())!.width).toBeLessThanOrEqual(390);
  await page.getByRole("tablist", { name: "Largeur de l'aperçu" }).getByRole("tab", { name: /Tablette/ }).click();
  const tablet = (await frame.boundingBox())!.width;
  expect(tablet).toBeLessThanOrEqual(768);
  expect(full).toBeGreaterThan(tablet - 1);
});

test("éditeur de schéma : Début et Fin sont étiquetés", async ({ page }) => {
  await login(page, "alice");
  const slug = `nav-lab-${Date.now().toString().slice(-6)}`;
  expect(await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [], nodes: [{ id: "a", type: "wait", name: "Pause", seconds: 1 }] })).toBe(201);
  await page.goto(`/workflows/${slug}/schema`);
  await expect(page.locator(".flow-endlabel", { hasText: "Début" })).toBeVisible();
  await expect(page.locator(".flow-endlabel", { hasText: "Fin" })).toBeVisible();
});
