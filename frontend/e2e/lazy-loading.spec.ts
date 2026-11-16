import { expect, test } from "@playwright/test";
import { goNav, login } from "./helpers";

// Pages et éditeur de code chargés à la demande : ce que voit réellement l'utilisateur (lecture seule : rien n'est créé).
const h1 = (page: import("@playwright/test").Page) => page.getByRole("heading", { level: 1 }).first();

test("routes directes après actualisation : chaque page différée s'affiche", async ({ page }) => {
  await login(page);
  const pages: [string, RegExp][] = [
    ["/workflows", /Workflows/],
    ["/connections", /Connexions/],
    ["/triggers", /Déclencheurs/],
    ["/library/forms", /Formulaires/],
    ["/library/controls", /Contrôles/],
    ["/admin", /Administration/],
  ];
  for (const [path, heading] of pages) {
    await page.goto(path);
    await page.reload();
    await expect(h1(page)).toContainText(heading, { timeout: 20_000 });
  }
});

test("le code d'une page n'est téléchargé qu'à sa première visite", async ({ page }) => {
  const seen: string[] = [];
  page.on("request", (r) => {
    const m = r.url().match(/\/src\/pages\/([A-Za-z]+)\.tsx/);
    if (m?.[1]) seen.push(m[1]);
  });
  await login(page, "admin");
  expect(seen).not.toContain("AdminPage");
  await goNav(page, "Administration");
  await expect(h1(page)).toContainText(/Administration/);
  expect(seen).toContain("AdminPage");
});

test("pendant le chargement : un message accessible, puis la page", async ({ page }) => {
  await login(page);
  await page.route("**/src/pages/ConnectionsPage.tsx*", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await route.continue();
  });
  await goNav(page, "Connexions");
  await expect(page.getByRole("status").filter({ hasText: "Chargement" })).toBeVisible();
  await expect(h1(page)).toContainText(/Connexions/, { timeout: 20_000 });
});

test("échec de téléchargement : message, bouton Recharger, puis la page revient", async ({ page }) => {
  await login(page);
  await page.route("**/src/pages/TriggersPage.tsx*", (route) => route.abort());
  await goNav(page, "Déclencheurs");
  const alert = page.getByRole("alert").filter({ hasText: "Impossible de charger" });
  await expect(alert).toBeVisible({ timeout: 20_000 });
  await page.unroute("**/src/pages/TriggersPage.tsx*");
  await alert.getByRole("button", { name: "Recharger la page" }).click();
  await expect(h1(page)).toContainText(/Déclencheurs/, { timeout: 20_000 });
});

test("navigation au clavier vers une page différée", async ({ page }) => {
  await login(page);
  await page.getByRole("navigation", { name: "Navigation principale" }).getByRole("link", { name: "Connexions", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(h1(page)).toContainText(/Connexions/, { timeout: 20_000 });
});

test("l'éditeur de code différé s'ouvre (source d'une action)", async ({ page }) => {
  await login(page);
  await goNav(page, "Bibliothèques");
  await page.getByLabel("Rechercher une action").fill("Transformation JSON");
  await page.getByRole("link", { name: "Transformation JSON", exact: true }).click();
  await page.getByRole("tab", { name: "Code" }).click();
  await expect(page.locator(".cm-editor").first()).toBeVisible({ timeout: 20_000 });
});

test("lecteur : une page réservée aux administrateurs ne plante pas et ne montre pas d'erreur de chargement", async ({ page }) => {
  await login(page, "bob");
  await page.goto("/admin");
  await expect(page.locator("main")).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: "Impossible de charger" })).toHaveCount(0);
});
