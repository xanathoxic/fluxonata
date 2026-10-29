import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, goNav, loginToCatalog as login } from "./helpers";

test("connexion refusée avec un mauvais mot de passe, message associé", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Identifiant").fill("alice");
  await page.getByLabel("Mot de passe").fill("faux");
  await page.getByRole("button", { name: "Se connecter" }).click();
  await expect(page.getByRole("alert")).toContainText("Identifiants incorrects");
});

test("utilisateur métier : catalogue → formulaire → exécution réelle → résultat", async ({ page }) => {
  await login(page);
  await expect(page.getByLabel("Environnement").locator("option:checked")).toHaveText("Recette"); // environnement toujours visible, en toutes lettres
  await page.getByRole("link", { name: "Statistiques de texte" }).click();
  await expect(page.getByLabel(/Texte/)).toBeVisible(); // la saisie est sur la fiche elle-même

  // champ obligatoire : erreur associée au champ
  await page.getByRole("button", { name: "Lancer l'automatisation" }).click();
  const field = page.getByLabel(/Texte/);
  await expect(field).toHaveAttribute("aria-invalid", "true");
  await expect(field).toHaveAccessibleDescription(/obligatoire/);

  await field.fill("un deux trois quatre");
  await page.getByRole("button", { name: "Lancer l'automatisation" }).click();

  await expect(page.getByRole("heading", { name: /Statistiques de texte/ })).toBeVisible();
  await expect(page.getByText("L'exécution s'est terminée avec succès.")).toBeVisible({ timeout: 90_000 }); // ≈ 8 s à vide (conteneur), jusqu'à 50 s quand d'autres tests lancent des conteneurs
  await expect(page.getByText("mots", { exact: true })).toBeVisible();
  await expect(page.locator("dd", { hasText: /^4$/ })).toBeVisible();
  await expectNoA11yViolations(page);

  await goNav(page, "Mes exécutions");
  await expect(page.getByRole("link", { name: "Statistiques de texte" }).first()).toBeVisible();
});

test("lecteur : le lancement est désactivé et expliqué", async ({ page }) => {
  await login(page, "bob");
  await page.getByRole("link", { name: "Statistiques de texte" }).click();
  await expect(page.getByRole("button", { name: "Lancer l'automatisation" })).toBeDisabled();
  await expect(page.locator("#no-run")).toBeVisible();
  await page.goto("/workflows/statistiques-de-texte/run");
  await expect(page.getByRole("button", { name: "Lancer l'automatisation" })).toBeDisabled();
  await expect(page.getByText("Votre rôle ne permet pas de lancer")).toBeVisible();
});

test("production : bandeau explicite en toutes lettres", async ({ page }) => {
  await login(page);
  await page.getByLabel("Environnement").selectOption("prod");
  await expect(page.getByRole("note")).toContainText("Production : les exécutions ont des effets réels.");
});

test("accessibilité : écrans de connexion et catalogue", async ({ page }) => {
  await page.goto("/");
  await expectNoA11yViolations(page);
  await login(page);
  await expectNoA11yViolations(page);
  await page.getByRole("link", { name: "Statistiques de texte" }).click();
  await expect(page.getByRole("heading", { name: "Statistiques de texte" })).toBeVisible();
  await expectNoA11yViolations(page);
});

test("clavier seul : lancer une automatisation sans souris", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "Statistiques de texte" }).focus();
  await page.keyboard.press("Enter");
  await page.getByLabel(/Texte/).focus();
  await page.keyboard.type("clavier seulement");
  await page.getByRole("button", { name: "Lancer l'automatisation" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("L'exécution s'est terminée avec succès.")).toBeVisible({ timeout: 90_000 }); // ≈ 8 s à vide (conteneur), jusqu'à 50 s quand d'autres tests lancent des conteneurs
});

test("page introuvable : message clair, focus sur le titre, chemins de sortie, accessible", async ({ page }) => {
  await login(page);
  await page.goto("/cette-page-n-existe-pas");
  await expect(page.getByRole("heading", { level: 1, name: "Page introuvable" })).toBeFocused();
  await expect(page.getByText("/cette-page-n-existe-pas")).toBeVisible();
  await expectNoA11yViolations(page);
  await page.goto("/workflows/workflow-inexistant");
  await expect(page.getByRole("heading", { level: 1, name: "Workflow introuvable" })).toBeVisible();
  await page.goto("/runs/00000000-0000-0000-0000-000000000000");
  await expect(page.getByRole("heading", { level: 1, name: "Exécution introuvable" })).toBeVisible();
  await page.getByRole("link", { name: "Retour à l'accueil" }).click();
  await expect(page.getByRole("heading", { level: 1, name: /Bonjour/ })).toBeVisible();
});

test("menu de gauche : se replie en rail d'icônes, reste accessible et utilisable, mémorisé", async ({ page }) => {
  await login(page);
  await page.evaluate(() => localStorage.removeItem("orch.sidebar"));
  await page.reload();
  const nav = page.getByRole("navigation", { name: "Navigation principale" });
  const main = page.locator("main#main");
  const w = async (l: ReturnType<Page["locator"]>) => Math.round((await l.boundingBox())!.width);
  expect(await w(nav)).toBeGreaterThan(190);
  const mainBefore = await w(main);

  const toggle = page.getByRole("button", { name: "Replier la navigation" });
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await toggle.click();
  await expect(page.getByRole("button", { name: "Déplier la navigation" })).toHaveAttribute("aria-expanded", "false");
  expect(await w(nav)).toBeLessThanOrEqual(80);
  expect(await w(main)).toBeGreaterThan(mainBefore + 120); // le contenu gagne la place
  // icônes seules, mais les noms restent (lecteurs d'écran) et les liens fonctionnent
  await expect(nav.getByRole("link", { name: "Workflows", exact: true })).toHaveAttribute("title", "Workflows");
  await nav.getByRole("link", { name: "Workflows", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Workflows" })).toBeVisible();
  await nav.getByRole("link", { name: "Bibliothèques", exact: true }).click();
  await page.getByRole("list", { name: "Bibliothèques" }).getByRole("link", { name: "Skills IA", exact: true }).click(); // les sous-menus restent atteignables
  await expect(page.getByRole("heading", { level: 1, name: "Skills IA" })).toBeVisible();
  await expectNoA11yViolations(page);

  await page.reload(); // mémorisé
  expect(await w(nav)).toBeLessThanOrEqual(80);
  await page.getByRole("button", { name: "Déplier la navigation" }).click();
  expect(await w(nav)).toBeGreaterThan(190);
  await expect(nav.getByRole("link", { name: "Accueil", exact: true })).toBeVisible();
});
