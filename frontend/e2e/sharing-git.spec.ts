import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, goNav, login } from "./helpers";

const uniq = () => `${Date.now()}`.slice(-7);

/** State set-up only (never what a test asserts): a JSON call to the API as the signed-in person. */
async function api(page: Page, method: string, path: string, body?: unknown): Promise<number> {
  return page.evaluate(async ({ method, path, body }) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    const r = await fetch(`/api/v1/workspaces/demo${path}`, { method, headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: body === undefined ? undefined : JSON.stringify(body) });
    return r.status;
  }, { method, path, body });
}

test("dossier privé : l'administrateur le partage, seules les personnes choisies le voient", async ({ browser }) => {
  const id = uniq();
  const folder = `Prive${id}`;
  const slug = `secret-${id}`;
  const admin = await (await browser.newContext()).newPage();
  await login(admin, "admin");
  expect(await api(admin, "POST", "/workflows/", { slug, name: `Secret ${id}`, folder })).toBe(201);
  await goNav(admin, "Workflows");
  await admin.getByRole("navigation", { name: "Dossiers de workflows" }).getByRole("button", { name: new RegExp(folder) }).click();
  await admin.getByRole("button", { name: "Partager le dossier sélectionné (rendre privé)" }).click();
  const dialog = admin.getByRole("dialog", { name: new RegExp(`Partage du dossier « ${folder} »`) });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel(/Dossier privé/).check();
  await dialog.getByLabel("alice").selectOption({ label: "Voir, lancer, modifier et publier" });
  await expectNoA11yViolations(admin);
  await dialog.getByRole("button", { name: "Enregistrer" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(admin.getByRole("navigation", { name: "Dossiers de workflows" }).getByRole("button", { name: new RegExp(`${folder} privé`) }).getByText("🔒")).toBeVisible(); // ce dossier-ci : d'autres dossiers privés peuvent subsister
  await expect(admin.getByRole("link", { name: `Modifier Secret ${id}` })).toBeVisible(); // l'administrateur garde l'accès

  const alice = await (await browser.newContext()).newPage();
  await login(alice, "alice");
  await goNav(alice, "Workflows");
  await expect(alice.getByRole("link", { name: `Modifier Secret ${id}` })).toBeVisible();

  const bob = await (await browser.newContext()).newPage();
  await login(bob, "bob");
  await goNav(bob, "Workflows");
  await expect(bob.getByRole("heading", { name: "Workflows", exact: true })).toBeVisible();
  await expect(bob.getByText(`Secret ${id}`)).toHaveCount(0);
  await expect(bob.getByRole("navigation", { name: "Dossiers de workflows" }).getByRole("button", { name: new RegExp(folder) })).toHaveCount(0);
  expect(await api(bob, "GET", `/workflows/${slug}/`)).toBe(404); // même réponse qu'un workflow qui n'existe pas

  // retour au dossier public : le lecteur le revoit
  expect(await api(admin, "DELETE", `/folder-access/?folder=${folder}`)).toBe(204);
  expect(await api(bob, "GET", `/workflows/${slug}/`)).toBe(200);
});

test("Git : configuration, opération exécutée par le worker et erreur claire sans réseau", async ({ page }) => {
  const id = uniq();
  await login(page, "admin");
  expect(await api(page, "POST", "/secrets/", { name: `gitok${id}`, value: "jeton-de-test-12345" })).toBe(201);
  // l'adresse doit se résoudre publiquement (contrôle SSRF à la création) : sans DNS, ce test n'a pas de sens
  const created = await api(page, "POST", "/connections/", { name: `forge${id}`, label: `Forge ${id}`, kind: "http_bearer", config: { base_url: "https://example.org" }, secret: `gitok${id}` });
  test.skip(created === 400, "adresse publique non résolue (pas de DNS)");
  expect(created).toBe(201);
  await goNav(page, "Administration");
  await page.getByRole("tab", { name: "Git", exact: true }).click();
  await page.getByLabel("Dépôt", { exact: true }).fill("exemple/depot");
  await page.getByLabel("Connexion (jeton d'accès)").selectOption({ label: `Forge ${id}` });
  await expectNoA11yViolations(page);
  await page.getByRole("button", { name: "Enregistrer la configuration" }).click();
  await expect(page.getByRole("button", { name: "Proposer les modifications (demande de fusion)" })).toBeVisible();
  await page.getByRole("button", { name: "Tester l'accès" }).click();
  // le worker prend l'opération ; ce dépôt n'existe pas : elle échoue avec un message clair (et sans jamais afficher le jeton)
  await expect(page.getByRole("row", { name: /Test d'accès.*Échouée/ }).first()).toBeVisible({ timeout: 45_000 });
  await expect(page.getByText("jeton-de-test-12345")).toHaveCount(0);
  await page.getByRole("button", { name: "Retirer la configuration" }).click();
  await expect(page.getByRole("button", { name: "Tester l'accès" })).toHaveCount(0);
});

test("éditeur : « Premier arrivé » et fichiers dans les étapes", async ({ page }) => {
  await login(page);
  await goNav(page, "Workflows");
  await page.getByRole("button", { name: "Nouveau workflow" }).click();
  await page.getByLabel("Nom du nouveau workflow").fill(`Course E2E ${uniq()}`);
  await page.getByRole("button", { name: "Créer et ouvrir l'éditeur" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Course E2E");
  await page.getByRole("button", { name: "Ajouter : Requête HTTP", exact: true }).click();
  await page.getByRole("tab", { name: "Requête", exact: true }).click();
  await expect(page.getByLabel("Enregistrer la réponse comme fichier")).toBeVisible();
  await page.getByLabel("Méthode").selectOption("POST");
  await page.getByLabel("Format du corps").selectOption("file");
  await expect(page.getByLabel("Fichier à envoyer")).toBeVisible();
  await page.getByRole("button", { name: "Ajouter : Premier arrivé", exact: true }).click();
  await page.getByRole("tab", { name: "Premier arrivé", exact: true }).click();
  await expect(page.getByLabel("Branches plus lentes")).toHaveValue("cancel");
  await expectNoA11yViolations(page);
});
