import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, goNav, login } from "./helpers";

test("exploitation : graphiques, règles d'alerte (panneau) et conservation (onglets), accessibilité", async ({ page }) => {
  await login(page, "admin");
  await goNav(page, "Exploitation");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Exploitation");
  // vue d'ensemble : chiffres clés et graphique (avec sa version en tableau)
  await expect(page.getByText("Taux d'échec", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Exécutions par jour" })).toBeVisible();
  const toggle = page.getByRole("button", { name: "Afficher en tableau" });
  if (await toggle.isVisible()) {
    await toggle.click();
    await expect(page.getByRole("table", { name: "Exécutions terminées par jour" })).toBeVisible();
    await page.getByRole("button", { name: "Afficher le graphique" }).click();
  }
  await expectNoA11yViolations(page);

  // alertes : création dans un panneau
  await page.getByRole("tab", { name: "Alertes", exact: true }).click();
  await page.getByRole("button", { name: "Nouvelle règle", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "Nouvelle règle d'alerte" });
  await expect(drawer).toBeVisible();
  const name = `Alerte ${Date.now().toString().slice(-6)}`;
  await drawer.getByLabel("Nom", { exact: true }).fill(name);
  await drawer.getByLabel("Adresse de destination").fill("https://93.184.216.34/services/secret");
  await expectNoA11yViolations(page);
  await drawer.getByRole("button", { name: "Créer la règle" }).click();
  await expect(drawer).toHaveCount(0);
  await expect(page.getByRole("cell", { name: new RegExp(`^${name}`) })).toBeVisible();
  await expect(page.getByText("93.184.216.34").first()).toBeVisible();
  await expect(page.getByText("services/secret")).toHaveCount(0); // l'adresse complète n'est jamais réaffichée
  await expectNoA11yViolations(page);

  // suppression avec confirmation
  await page.getByRole("button", { name: `Supprimer la règle ${name}` }).click();
  const confirm = page.getByRole("dialog", { name: new RegExp(`Supprimer la règle « ${name} »`) });
  await expect(confirm).toBeVisible();
  await confirm.getByRole("button", { name: "Supprimer la règle" }).click();
  await expect(page.getByRole("cell", { name: new RegExp(`^${name}`) })).toHaveCount(0);

  // conservation
  await page.getByRole("tab", { name: "Conservation", exact: true }).click();
  await page.getByLabel("Jours de conservation").fill("365");
  await page.getByRole("button", { name: "Enregistrer" }).click();
  await expect(page.getByText("✔ Enregistré.")).toBeVisible();
  await expectNoA11yViolations(page);
});
