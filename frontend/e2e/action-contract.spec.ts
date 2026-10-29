import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, goNav, login } from "./helpers";

const uniq = () => `${Date.now()}`.slice(-7);

// Le contrat d'une action : ce qu'elle fait, chaque entrée et chaque sortie (type, rôle), ou l'absence d'entrée / de sortie.
test("contrat d'une action : lisible sur la page de l'action, et obligatoire à la publication", async ({ page }) => {
  const id = uniq();
  await login(page);
  await goNav(page, "Bibliothèques");
  await page.getByLabel("Rechercher une action").fill("Statistiques de texte"); // la liste est paginée : on cherche
  const row = page.getByRole("row", { name: /Statistiques de texte/ });
  await expect(row).toContainText("✔ Complet");

  await page.getByRole("link", { name: "Statistiques de texte", exact: true }).click();
  const contract = page.getByRole("region", { name: "Contrat de l'action" });
  await expect(contract.getByRole("heading", { name: "Ce que fait l'action" })).toBeVisible();
  await expect(contract).toContainText("mot le plus long");
  const inputs = contract.getByRole("table", { name: "Entrées" });
  await expect(inputs.getByRole("row", { name: /^text/ })).toContainText("texte");
  await expect(inputs.getByRole("row", { name: /^text/ })).toContainText("Oui"); // obligatoire
  await expect(inputs.getByRole("row", { name: /^text/ })).toContainText("Le texte à analyser.");
  await expect(contract.getByRole("table", { name: "Sorties" }).getByRole("row", { name: /words/ })).toContainText("Nombre de mots");
  await expect(contract).toContainText("Aucun effet externe");
  await expectNoA11yViolations(page);

  // publier une action sans description : refusé, avec ce qu'il faut ajouter
  await goNav(page, "Bibliothèques");
  await page.getByRole("button", { name: "Nouvelle action" }).click();
  const dlg = page.getByRole("dialog", { name: "Nouvelle action" });
  await dlg.getByLabel(/Identifiant/).fill(`contrat-${id}`);
  await dlg.getByLabel("Nom", { exact: true }).fill(`Contrat ${id}`);
  await dlg.getByRole("button", { name: /Publier la version/ }).click();
  await expect(dlg.getByRole("alert")).toContainText("Le contrat n'est pas complet");
  await expect(dlg.getByRole("alert")).toContainText("Description de l'action");
  await expectNoA11yViolations(page);

  await dlg.getByLabel(/Ce que fait l'action/).fill("Double un nombre entier et renvoie le résultat, sans rien modifier d'autre.");
  await dlg.getByRole("button", { name: /Publier la version/ }).click();
  await expect(page).toHaveURL(new RegExp(`/library/actions/contrat-${id}$`));
  const created = page.getByRole("region", { name: "Contrat de l'action" });
  await expect(created.getByRole("table", { name: "Sorties" }).getByRole("row", { name: /double/ })).toContainText("multiplié par deux");
  await expect(page.getByRole("note")).toHaveCount(0); // aucun avertissement « contrat incomplet »
});
