import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, goNav, login } from "./helpers";

const tab = (page: Page, name: string) => page.getByRole("tab", { name }).click();
const uniq = () => `${Date.now()}`.slice(-7);

test("plate-forme : créer un runner (jeton affiché une fois), pause, régénération, suppression", async ({ page }) => {
  const name = `rn${uniq()}`;
  await login(page, "admin");
  await goNav(page, "Plate-forme");
  await tab(page, "Runners");
  await expectNoA11yViolations(page);

  await page.getByRole("button", { name: "Nouveau runner" }).click();
  const form = page.getByRole("dialog", { name: "Nouveau runner" });
  await form.getByLabel("Nom", { exact: true }).fill(name);
  await form.getByLabel("Étiquettes").fill("linux, gpu");
  await form.getByRole("radio", { name: "Partagé (tous les espaces qui acceptent les runners partagés)" }).check();
  await expectNoA11yViolations(page);
  await form.getByRole("button", { name: "Créer le runner" }).click();

  const token = page.getByRole("dialog", { name: `Runner « ${name} » créé` });
  await expect(token).toBeVisible();
  await expect(token.getByText(/orr_[A-Za-z0-9_-]{20,}/).first()).toBeVisible();
  await expect(token.getByLabel("Commande pour démarrer l'agent sur l'hôte")).toContainText("runner/agent.py");
  await expect(token.getByLabel("Commandes pour démarrer l'agent dans un conteneur")).toContainText("--group-add");
  await expectNoA11yViolations(page);
  await token.getByRole("button", { name: "J'ai copié le jeton" }).click();

  const row = page.getByRole("row", { name: new RegExp(name) });
  await expect(row).toContainText("Jamais connecté");
  await expect(row).toContainText("gpu");
  // le jeton n'est plus nulle part dans la page
  await expect(page.locator("body")).not.toContainText(/orr_[A-Za-z0-9_-]{20,}/);

  await row.getByRole("button", { name: `Mettre en pause le runner ${name}` }).click();
  await expect(row).toContainText("En pause");
  await row.getByRole("button", { name: `Reprendre le runner ${name}` }).click();
  await expect(row).not.toContainText("En pause");

  await row.getByRole("button", { name: `Régénérer le jeton du runner ${name}` }).click();
  await page.getByRole("dialog", { name: /Régénérer le jeton/ }).getByRole("button", { name: "Régénérer" }).click();
  const second = page.getByRole("dialog", { name: /créé|régénéré/i });
  await expect(second.getByText(/orr_[A-Za-z0-9_-]{20,}/).first()).toBeVisible();
  await second.getByRole("button", { name: "J'ai copié le jeton" }).click();

  await row.getByRole("button", { name: `Supprimer le runner ${name}` }).click();
  await page.getByRole("dialog", { name: /Supprimer/ }).getByRole("button", { name: "Supprimer le runner" }).click();
  await expect(page.getByRole("row", { name: new RegExp(name) })).toHaveCount(0);
});

test("administrateur d'espace : runner dédié, runners partagés désactivables", async ({ page }) => {
  const name = `ws${uniq()}`;
  await login(page, "admin");
  await goNav(page, "Administration");
  await tab(page, "Runners");
  const shared = page.getByLabel(/Autoriser les runners partagés/);
  await expect(shared).toBeChecked();
  await expectNoA11yViolations(page);

  await page.getByRole("button", { name: "Nouveau runner" }).click();
  const form = page.getByRole("dialog", { name: "Nouveau runner" });
  await expect(form).toContainText("Dédié à l'espace");
  await form.getByLabel("Nom", { exact: true }).fill(name);
  await form.getByRole("button", { name: "Créer le runner" }).click();
  const token = page.getByRole("dialog", { name: `Runner « ${name} » créé` });
  await expect(token.getByText(/orr_/).first()).toBeVisible();
  await token.getByRole("button", { name: "J'ai copié le jeton" }).click();

  // sans runner dédié en ligne, désactiver les runners partagés doit prévenir que les actions attendront
  // (toujours restauré : un espace sans runner partagé n'exécute plus rien pour les autres tests)
  try {
    await shared.click(); // case contrôlée : l'état change après la réponse du serveur
    await expect(shared).not.toBeChecked();
    await expect(page.getByRole("alert").filter({ hasText: "restent en attente" })).toBeVisible();
  } finally {
    if (!(await shared.isChecked())) await shared.click();
    await expect(shared).toBeChecked();
  }

  await page.getByRole("row", { name: new RegExp(name) }).getByRole("button", { name: `Supprimer le runner ${name}` }).click();
  await page.getByRole("dialog", { name: /Supprimer/ }).getByRole("button", { name: "Supprimer le runner" }).click();
  await expect(page.getByRole("row", { name: new RegExp(name) })).toHaveCount(0);
});

test("un concepteur n'a accès à aucune gestion de runner", async ({ page }) => {
  await login(page, "alice");
  const codes = await page.evaluate(async () => {
    const get = (u: string) => fetch(u, { credentials: "same-origin" }).then((r) => r.status);
    return [await get("/api/v1/platform/runners/"), await get("/api/v1/workspaces/vitrine/runners/")];
  });
  expect(codes[0]).toBeGreaterThanOrEqual(403);
  expect(codes[1]).toBe(403);
});
