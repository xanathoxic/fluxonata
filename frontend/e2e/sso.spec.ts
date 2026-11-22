import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, goNav, login } from "./helpers";

const uniq = () => `${Date.now()}`.slice(-7);

test("SSO : créer un fournisseur, régler ses droits, simuler, l'activer ; le bouton paraît sur la page de connexion ; les erreurs y reviennent", async ({
  page,
  browser,
}) => {
  const id = uniq();
  const slug = `e2e-${id}`;
  await login(page, "admin");
  await goNav(page, "Plate-forme");
  await page.getByRole("tab", { name: /Authentification/ }).click();
  await page
    .getByRole("button", { name: /Ajouter OpenID Connect|^OpenID Connect/ })
    .first()
    .click();

  const dialog = page.getByRole("dialog"); // son titre change à la création (« Nouveau fournisseur… » puis « Fournisseur « nom » »)
  await dialog.locator("#sso-name").fill(`Annuaire ${id}`);
  await dialog.locator("#sso-slug").fill(slug);
  await dialog.locator("#sso-issuer").fill("https://idp.example.org/realms/e2e");
  await dialog.locator("#sso-cid").fill("fluxonata");
  await dialog.locator("#sso-secret").fill("secret-e2e-jamais-affiche");
  await expectNoA11yViolations(page);
  await dialog.getByRole("button", { name: "Créer le fournisseur" }).click();

  // après la création : les droits ; le secret n'est plus jamais affiché
  await expect(dialog.getByText("Groupes du fournisseur → droits")).toBeVisible();
  await dialog.getByRole("button", { name: "Ajouter une règle" }).click();
  await dialog.getByLabel("Groupe du fournisseur").fill("orch-concepteurs");
  await dialog.getByLabel("Espace", { exact: true }).selectOption({ index: 1 });
  await dialog.getByLabel("Rôle", { exact: true }).selectOption("designer");
  await dialog.getByRole("button", { name: "Enregistrer les droits" }).click();
  await expect(dialog.getByText("Enregistré.").first()).toBeVisible();
  await dialog.getByLabel(/Groupes d'une personne/).fill("orch-concepteurs\nautre");
  await dialog.getByRole("button", { name: /Simuler avec/ }).click();
  await expect(dialog.getByText("Connexion autorisée")).toBeVisible();
  await expect(dialog.getByText(/« orch-concepteurs » correspond/)).toBeVisible();
  await expectNoA11yViolations(page);
  await dialog.getByRole("tab", { name: /Adresses à donner/ }).click();
  await expect(dialog.getByText(`/api/v1/auth/sso/${slug}/callback/`)).toBeVisible();
  await dialog.getByRole("tab", { name: /Comptes et droits/ }).click();
  await dialog.getByLabel("Fournisseur actif").click(); // l'état change à la réponse du serveur
  await expect(dialog.getByText(/Actif : le bouton est sur la page de connexion/)).toBeVisible();
  expect(await page.content()).not.toContain("secret-e2e-jamais-affiche");
  await dialog.getByRole("button", { name: "Fermer" }).last().click();
  await expect(page.getByRole("region", { name: `Annuaire ${id}` })).toBeVisible();

  // la page de connexion propose le bouton ; un échec de connexion y revient avec son message
  const anon = await (await browser.newContext()).newPage();
  await anon.goto("/");
  await expect(anon.getByRole("link", { name: `Se connecter avec Annuaire ${id}` })).toHaveAttribute("href", `/api/v1/auth/sso/${slug}/login/`);
  await anon.goto("/?sso_error=Votre+compte+n%27appartient+%C3%A0+aucun+groupe");
  await expect(anon.getByRole("alert").filter({ hasText: "aucun groupe" })).toBeVisible();
  await expect(anon).toHaveURL(/\/$/);
  await expectNoA11yViolations(anon);

  // nettoyage : le fournisseur est supprimé (les autres scénarios ne doivent pas voir de bouton SSO)
  await page.getByRole("button", { name: `Supprimer Annuaire ${id}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Supprimer" }).click();
  await expect(page.getByRole("region", { name: `Annuaire ${id}` })).toHaveCount(0);
});

test("SSO : le serveur refuse les appels d'une personne qui n'est pas administrateur de la plate-forme", async ({ page }) => {
  await login(page, "alice");
  const status = await page.evaluate(async () => (await fetch("/api/v1/platform/sso/providers/", { credentials: "same-origin" })).status);
  expect(status).toBe(403);
});
