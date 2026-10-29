import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, goNav, login } from "./helpers";

const uniq = () => `${Date.now()}`.slice(-7);

async function signIn(page: Page, user: string, password: string) {
  await page.goto("/");
  await page.getByLabel("Identifiant").fill(user);
  await page.getByLabel("Mot de passe").fill(password);
  await page.getByRole("button", { name: "Se connecter" }).click();
}

test("plate-forme : créer un utilisateur dans deux espaces, mot de passe provisoire, première connexion, désactivation", async ({ page, browser }) => {
  const name = `usr${uniq()}`;
  const mine = `Mon-mot-de-passe-${uniq()}`;
  await login(page, "admin");
  await goNav(page, "Plate-forme");
  await page.getByRole("tab", { name: "Utilisateurs" }).click();
  await expect(page.getByRole("cell", { name: /alice/ }).first()).toBeVisible();
  await expectNoA11yViolations(page);

  // création : deux espaces, rôles différents, mot de passe généré
  await page.getByRole("button", { name: "Nouvel utilisateur" }).click();
  const form = page.getByRole("dialog", { name: "Nouvel utilisateur" });
  await form.getByLabel("Identifiant").fill(name);
  await form.getByLabel("Adresse e-mail (facultatif)").fill(`${name}@example.org`);
  await form.getByRole("button", { name: "Ajouter un espace" }).click();
  await form.getByRole("button", { name: "Ajouter un espace" }).click();
  await form.getByLabel(/^Rôle dans/).first().selectOption("viewer");
  await form.getByLabel(/^Rôle dans/).nth(1).selectOption("designer");
  await expectNoA11yViolations(page);
  await form.getByRole("button", { name: "Créer l'utilisateur" }).click();
  const secret = page.getByRole("dialog", { name: "Utilisateur créé" });
  const temp = (await secret.getByLabel("Mot de passe provisoire").innerText()).trim();
  expect(temp.length).toBeGreaterThanOrEqual(16);
  await expectNoA11yViolations(page);
  await secret.getByRole("button", { name: "J'ai transmis le mot de passe" }).click();
  await page.getByLabel("Rechercher un utilisateur").fill(name); // la liste est paginée
  const row = page.getByRole("row", { name: new RegExp(name) });
  await expect(row).toContainText("Mot de passe provisoire");
  await expect(page.locator("body")).not.toContainText(temp); // affiché une seule fois

  // première connexion : seul le changement de mot de passe est possible
  const person = await (await browser.newContext()).newPage();
  await signIn(person, name, temp);
  await expect(person.getByRole("heading", { name: "Choisissez votre mot de passe" })).toBeVisible({ timeout: 20_000 });
  await expect(person.getByRole("navigation", { name: "Navigation principale" })).toHaveCount(0);
  await expectNoA11yViolations(person);
  await person.getByLabel("Mot de passe provisoire").fill(temp);
  await person.getByLabel("Nouveau mot de passe", { exact: true }).fill("court");
  await person.getByLabel("Confirmer le nouveau mot de passe").fill("court");
  await person.getByRole("button", { name: "Enregistrer le mot de passe" }).click();
  await expect(person.getByRole("alert")).toContainText(/trop court|12 caractères/i);
  await person.getByLabel("Nouveau mot de passe", { exact: true }).fill(mine);
  await person.getByLabel("Confirmer le nouveau mot de passe").fill(mine);
  await person.getByRole("button", { name: "Enregistrer le mot de passe" }).click();
  await expect(person.getByRole("heading", { name: /Bonjour/ })).toBeVisible({ timeout: 20_000 });
  await person.getByRole("link", { name: /mon compte/i }).click();
  await expect(person.getByRole("heading", { name: "Mon compte" })).toBeVisible();
  await expect(person.getByRole("navigation", { name: "Navigation principale" }).getByRole("link", { name: "Plate-forme" })).toHaveCount(0);

  // l'administrateur change un rôle, retire un espace
  await page.reload();
  await page.getByRole("tab", { name: "Utilisateurs" }).click();
  await page.getByLabel("Rechercher un utilisateur").fill(name);
  await page.getByRole("button", { name: `Gérer les espaces de ${name}` }).click();
  const spaces = page.getByRole("dialog", { name: `Espaces de ${name}` });
  await expectNoA11yViolations(page);
  await spaces.getByRole("button", { name: new RegExp(`^Retirer ${name} de l'espace`) }).first().click();
  await expect(spaces.getByRole("row")).toHaveCount(2); // en-tête + 1 espace restant
  await spaces.getByRole("button", { name: "Terminé" }).click();

  // désactivation : la session ouverte s'arrête, la connexion est refusée
  await page.getByRole("button", { name: `Désactiver ${name}` }).click();
  await page.getByRole("dialog", { name: new RegExp(`Désactiver « ${name} »`) }).getByRole("button", { name: "Désactiver le compte" }).click();
  await expect(page.getByRole("row", { name: new RegExp(name) })).toContainText("Désactivé");
  await person.reload();
  await expect(person.getByRole("heading", { name: "Connexion" })).toBeVisible({ timeout: 20_000 });
  await signIn(person, name, mine);
  await expect(person.getByRole("alert")).toContainText("Identifiants incorrects");

  // réactivation + réinitialisation du mot de passe
  await page.getByRole("button", { name: `Réactiver ${name}` }).click();
  await page.getByRole("dialog", { name: new RegExp(`Réactiver « ${name} »`) }).getByRole("button", { name: "Réactiver le compte" }).click();
  await expect(page.getByRole("row", { name: new RegExp(name) })).toContainText("Actif");
  await page.getByRole("button", { name: `Réinitialiser le mot de passe de ${name}` }).click();
  await page.getByRole("dialog", { name: /Réinitialiser le mot de passe/ }).getByRole("button", { name: "Réinitialiser" }).click();
  const again = page.getByRole("dialog", { name: "Mot de passe réinitialisé" });
  const temp2 = (await again.getByLabel("Mot de passe provisoire").innerText()).trim();
  await again.getByRole("button", { name: "J'ai transmis le mot de passe" }).click();
  await signIn(person, name, mine);
  await expect(person.getByRole("alert")).toContainText("Identifiants incorrects"); // l'ancien mot de passe ne marche plus
  await signIn(person, name, temp2);
  await expect(person.getByRole("heading", { name: "Choisissez votre mot de passe" })).toBeVisible({ timeout: 20_000 });
});

test("on ne peut pas se désactiver soi-même ; un concepteur n'a ni l'onglet ni l'API", async ({ page, browser }) => {
  await login(page, "admin");
  await goNav(page, "Plate-forme");
  await page.getByRole("tab", { name: "Utilisateurs" }).click();
  await page.getByRole("button", { name: "Désactiver admin" }).click();
  const dlg = page.getByRole("dialog", { name: /Désactiver « admin »/ });
  await dlg.getByRole("button", { name: "Désactiver le compte" }).click();
  await expect(dlg.getByRole("alert")).toContainText("propre compte");
  await dlg.getByRole("button", { name: "Annuler" }).click();

  const designer = await (await browser.newContext()).newPage();
  await login(designer, "alice");
  const codes = await designer.evaluate(async () => (await fetch("/api/v1/platform/users/", { credentials: "same-origin" })).status);
  expect(codes).toBe(403);
  await expect(designer.getByRole("navigation", { name: "Navigation principale" }).getByRole("link", { name: "Plate-forme" })).toHaveCount(0);
});
