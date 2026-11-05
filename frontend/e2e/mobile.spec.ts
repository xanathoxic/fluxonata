import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, login } from "./helpers";

/** Téléphone (390 × 844) : en-tête d'une ligne, espace et environnement dans le tiroir, arbre replié, KPI sur 2 colonnes, tableaux en cartes. */
test.use({ viewport: { width: 390, height: 844 } });

test("en-tête compact, tiroir avec espace et environnement, pas de défilement horizontal", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Identifiant").fill("alice");
  await page.getByLabel("Mot de passe").fill("demo-fluxonata");
  await page.getByRole("button", { name: "Se connecter" }).click();
  await expect(page.getByRole("heading", { name: /Bonjour/ })).toBeVisible({ timeout: 20_000 });
  const head = (await page.locator("header.topbar").boundingBox())!;
  expect(head.height).toBeLessThanOrEqual(56);
  await expect(page.locator(".env-stripe")).toBeVisible();
  await expect(page.getByLabel("Espace", { exact: true })).toBeHidden(); // plus dans l'en-tête
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole("button", { name: "Menu" }).click();
  const drawer = page.getByRole("navigation", { name: "Navigation principale" });
  await expect(drawer.getByLabel("Espace")).toBeVisible();
  await expect(drawer.getByLabel("Environnement")).toBeVisible();
  await expect(drawer.getByRole("button", { name: "Se déconnecter" })).toBeVisible();
  await expectNoA11yViolations(page);
});

test("workflows : arbre de dossiers replié, liste d'abord ; tableaux en cartes", async ({ page }) => {
  await login(page, "alice");
  await page.goto("/workflows");
  const fold = page.locator("details.ft-mobile > summary");
  await expect(fold).toContainText("Dossier : Tous");
  await expect(page.getByRole("navigation", { name: "Dossiers de workflows" })).toBeHidden(); // replié : l'arbre n'occupe pas l'écran
  const table = page.getByRole("table").first();
  await expect(table).toBeVisible();
  await expect(table.locator("td").first()).toHaveCSS("display", "block"); // carte
  await expect(table.locator("tbody tr").first().locator("td[data-label]").nth(1)).toHaveAttribute("data-label", /.+/);
  await fold.click();
  await expect(page.getByRole("navigation", { name: "Dossiers de workflows" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
