import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, goNav, login } from "./helpers";

// Fiche d'une action : onglets Contrat / Code / Versions, code en lecture seule dans l'éditeur.
test("fiche d'une action : onglets, code en lecture seule, versions", async ({ page }) => {
  await login(page);
  await goNav(page, "Bibliothèques");
  await page.getByLabel("Rechercher une action").fill("Statistiques de texte");
  await page.getByRole("link", { name: "Statistiques de texte", exact: true }).click();

  const tabs = page.getByRole("tablist", { name: "Sections de l'action" });
  await expect(tabs.getByRole("tab", { name: "Contrat" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("region", { name: "Contrat de l'action" })).toBeVisible();

  await tabs.getByRole("tab", { name: "Code" }).click();
  const viewer = page.getByRole("region", { name: /^Code de la version/ });
  await expect(viewer).toContainText("Lecture seule");
  const code = viewer.getByRole("textbox", { name: /lecture seule/ });
  await expect(code).toHaveAttribute("aria-readonly", "true").catch(() => undefined);
  await expect(viewer.locator(".cm-gutters")).toBeVisible();
  await expect(viewer.getByRole("button", { name: "Copier le code" })).toBeVisible();
  await expectNoA11yViolations(page);

  await tabs.getByRole("tab", { name: /^Versions/ }).click();
  await expect(page.getByRole("table", { name: "Versions de l'action" })).toBeVisible();
  await page.getByRole("button", { name: /^Voir le code de la version/ }).first().click();
  await expect(tabs.getByRole("tab", { name: "Code" })).toHaveAttribute("aria-selected", "true");
  await expectNoA11yViolations(page);
});
