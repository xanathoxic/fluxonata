import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, goNav, login } from "./helpers";

const uniq = () => `${Date.now()}`.slice(-7);

test("plate-forme : coffre externe — réglages, guide de mise en place, test expliqué, accès par espace, accessibilité", async ({ page }) => {
  await login(page, "admin");
  await goNav(page, "Plate-forme");
  await page.getByRole("tab", { name: "Coffre externe" }).click();
  await expect(page.getByRole("heading", { name: "Connexion à Vault" })).toBeVisible();
  await expectNoA11yViolations(page);

  await page.getByRole("button", { name: "Guide de mise en place" }).click();
  const guide = page.getByRole("dialog", { name: /Mettre Vault en place/ });
  await expect(guide.getByLabel("Commandes Vault")).toContainText("vault auth enable approle");
  await expectNoA11yViolations(page);
  await guide.getByRole("button", { name: "Fermer", exact: true }).first().click();

  // un coffre inexistant : l'enregistrement marche, le test explique le blocage (sans rien laisser entendre sur un secret)
  await page.getByLabel("Adresse de Vault").fill("https://vault.invalid:8200");
  await page.getByLabel("role_id de l'AppRole").fill("role-e2e");
  await page.getByLabel("secret_id de l'AppRole").fill("secret-id-e2e-0000");
  await page.getByLabel("Activer le coffre externe").uncheck(); // état de départ des autres tests : coffre externe non disponible
  await page.getByRole("button", { name: "Enregistrer", exact: true }).click();
  await expect(page.getByText("Enregistré : testez la connexion.")).toBeVisible();
  await page.getByRole("button", { name: "Tester la connexion" }).click();
  const result = page.getByRole("dialog", { name: "Test de la connexion à Vault" });
  await expect(result.getByText("Joindre le serveur")).toBeVisible({ timeout: 30_000 });
  await expect(result.getByText(/VAULT_/)).toBeVisible();
  expect(await page.content()).not.toContain("secret-id-e2e-0000"); // le secret_id n'est jamais réaffiché
  await expectNoA11yViolations(page);
  await result.getByRole("button", { name: "Fermer", exact: true }).first().click();

  // accès par espace : motifs validés par le serveur
  await page.getByRole("button", { name: /Modifier les accès de l'espace Espace démo/ }).click();
  const grants = page.getByRole("dialog", { name: /Accès au coffre/ });
  await grants.getByRole("button", { name: "Ajouter un motif" }).click();
  await grants.getByLabel("Motif 1", { exact: true }).fill("*");
  await grants.getByRole("button", { name: "Enregistrer les accès" }).click();
  await expect(grants.getByRole("alert")).toContainText("point de montage");
  await grants.getByLabel("Motif 1", { exact: true }).fill("secret/e2e/*");
  await grants.getByRole("button", { name: "Enregistrer les accès" }).click();
  await expect(grants).toHaveCount(0);
  await expect(page.getByText("secret/e2e/*")).toBeVisible();
  // état propre : on retire le motif de test
  await page.getByRole("button", { name: /Modifier les accès de l'espace Espace démo/ }).click();
  await page.getByRole("dialog", { name: /Accès au coffre/ }).getByRole("button", { name: "Retirer le motif 1" }).click();
  await page.getByRole("dialog", { name: /Accès au coffre/ }).getByRole("button", { name: "Enregistrer les accès" }).click();
  await expect(page.getByText("secret/e2e/*")).toHaveCount(0);
});

test("administration : secrets — choix du coffre, création, remplacement et suppression d'un secret du coffre de Fluxonata", async ({ page }) => {
  const name = `e2e-secret-${uniq()}`;
  await login(page, "admin");
  await goNav(page, "Administration");
  await page.getByRole("tab", { name: "Secrets" }).click();
  await expect(page.getByRole("heading", { name: "Secrets de l'espace" })).toBeVisible();
  await expectNoA11yViolations(page);

  await page.getByRole("button", { name: "Nouveau secret" }).click();
  const dlg = page.getByRole("dialog", { name: "Nouveau secret" });
  await dlg.getByLabel("Nom du secret").fill(name);
  await expect(dlg.getByRole("radio", { name: /Coffre de Fluxonata/ })).toBeChecked();
  await expect(dlg.getByRole("radio", { name: /Vault \(HashiCorp\)/ })).toBeDisabled(); // coffre externe non activé
  await expect(dlg.getByText(/Non disponible/)).toBeVisible();
  await dlg.getByLabel("Valeur du secret").fill("valeur-e2e-secrete");
  await expectNoA11yViolations(page);
  await dlg.getByRole("button", { name: "Créer le secret" }).click();
  await expect(dlg).toHaveCount(0); // la fenêtre se ferme quand le secret est créé

  await page.getByLabel("Rechercher un secret").fill(name);
  const row = page.getByRole("row", { name: new RegExp(name) });
  await expect(row).toContainText("Coffre de Fluxonata");
  expect(await page.content()).not.toContain("valeur-e2e-secrete"); // la valeur n'est jamais relue
  await row.getByRole("button", { name: `Remplacer la valeur du secret ${name}` }).click();
  await page.getByLabel("Nouvelle valeur").fill("autre-valeur-e2e");
  await page.getByRole("dialog", { name: /Remplacer/ }).getByRole("button", { name: "Remplacer", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await row.getByRole("button", { name: `Supprimer le secret ${name}` }).click();
  await page.getByRole("dialog", { name: "Supprimer ce secret ?" }).getByRole("button", { name: "Supprimer le secret" }).click();
  await expect(page.getByRole("row", { name: new RegExp(name) })).toHaveCount(0);
});
