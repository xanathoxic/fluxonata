import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, goNav, login } from "./helpers";

// Jeux de test d'une action : enregistrer, modifier, supprimer, et les retrouver dans « Lancer un essai ».
test("jeux de test : créer, proposer au lancement d'un essai, modifier, supprimer", async ({ page }) => {
  const name = `Cas ${`${Date.now()}`.slice(-6)}`;
  await login(page);
  await goNav(page, "Bibliothèques");
  await page.getByLabel("Rechercher une action").fill("Statistiques de texte");
  await page.getByRole("link", { name: "Statistiques de texte", exact: true }).click();
  await page.getByRole("tab", { name: "Gestion" }).click();

  await page.getByRole("button", { name: "Ajouter un jeu de test" }).click();
  const dlg = page.getByRole("dialog", { name: "Nouveau jeu de test" });
  await dlg.getByRole("button", { name: "Enregistrer" }).click(); // nom et entrée obligatoires
  await expect(dlg.getByText("Donnez un nom au jeu de test.")).toBeVisible();
  await dlg.getByLabel("Nom du jeu de test").fill(name);
  await dlg.getByLabel(/^texte/i).first().fill("un texte de démonstration");
  await expectNoA11yViolations(page);
  await dlg.getByRole("button", { name: "Enregistrer" }).click();
  const row = page.getByRole("row", { name: new RegExp(name) });
  await expect(row).toContainText("un texte de démonstration");
  await expect(row).toContainText("Valide");

  // proposé au lancement d'un essai
  await page.getByRole("link", { name: "Lancer un essai" }).click();
  await page.getByLabel("Jeux de test").selectOption({ label: name });
  await expect(page.getByLabel(/^texte/i).first()).toHaveValue("un texte de démonstration");
  await expectNoA11yViolations(page);
  await page.goBack();

  // modifier
  await page.getByRole("tab", { name: "Gestion" }).click();
  await page.getByRole("button", { name: `Modifier le jeu de test ${name}` }).click();
  const edit = page.getByRole("dialog", { name: "Modifier le jeu de test" });
  await edit.getByLabel(/^texte/i).first().fill("texte modifié");
  await edit.getByRole("button", { name: "Enregistrer" }).click();
  await expect(page.getByRole("row", { name: new RegExp(name) })).toContainText("texte modifié");

  // supprimer
  await page.getByRole("button", { name: `Supprimer le jeu de test ${name}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Supprimer" }).click();
  await expect(page.getByRole("row", { name: new RegExp(name) })).toHaveCount(0);
});

// Les entrées de type objet ou liste se saisissent en JSON (et non « [object Object] »).
test("jeux de test : entrées objet et liste en JSON", async ({ page }) => {
  const name = `Json ${`${Date.now()}`.slice(-6)}`;
  await login(page);
  await goNav(page, "Bibliothèques");
  await page.getByLabel("Rechercher une action").fill("Transformation JSON");
  await page.getByRole("link", { name: "Transformation JSON", exact: true }).click();
  await page.getByRole("tab", { name: "Gestion" }).click();
  await page.getByRole("button", { name: "Ajouter un jeu de test" }).click();
  const dlg = page.getByRole("dialog", { name: "Nouveau jeu de test" });
  await dlg.getByLabel("Nom du jeu de test").fill(name);
  await dlg.getByLabel(/Objet source/).fill('{ pas du json');
  await dlg.getByLabel(/Champs à conserver/).fill('["x"]');
  await dlg.getByRole("button", { name: "Enregistrer" }).click();
  await expect(dlg.getByText(/JSON invalide/).first()).toBeVisible();
  await dlg.getByLabel(/Objet source/).fill('{ "x": 1, "y": 2 }');
  await expect(dlg.getByText("JSON valide ✓").first()).toBeVisible();
  await expectNoA11yViolations(page);
  await dlg.getByRole("button", { name: "Enregistrer" }).click();
  const row = page.getByRole("row", { name: new RegExp(name) });
  await expect(row).toContainText('"data":{"x":1,"y":2}');
  await page.getByRole("button", { name: `Modifier le jeu de test ${name}` }).click();
  await expect(page.getByRole("dialog").getByLabel(/Objet source/)).toContainText('"x": 1');
  await page.getByRole("dialog").getByRole("button", { name: "Annuler" }).click();
  await page.getByRole("button", { name: `Supprimer le jeu de test ${name}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Supprimer" }).click();
  await expect(row).toHaveCount(0);
});
