import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, login, publishViaApi } from "./helpers";

test("formulaire public : création, page sans connexion, envoi, accessibilité", async ({ page, browser }) => {
  await login(page);
  await page.goto("/triggers");
  await page.getByRole("tab", { name: "Formulaires publics" }).click();
  await page.getByRole("button", { name: "Nouveau formulaire public" }).click();
  const id = Date.now().toString().slice(-6);
  await page.getByLabel("Nom interne").fill(`Form ${id}`);
  await page.getByLabel("Titre affiché").fill(`Demande ${id}`);
  const select = page.getByLabel("Workflow", { exact: true });
  await select.selectOption({ index: 1 });
  await page.getByRole("button", { name: "Créer le formulaire" }).click();
  const link = (await page.getByRole("dialog").getByText(/\/f\//).first().textContent()) ?? "";
  const url = link.slice(link.indexOf("http"));
  expect(url).toContain("/f/");
  await page.getByRole("button", { name: "Fermer", exact: true }).last().click();

  const anon = await browser.newContext(); // pas de session
  const pub = await anon.newPage();
  await pub.goto(url);
  await expect(pub.getByRole("heading", { level: 1 })).toContainText(`Demande ${id}`);
  await expect(pub.getByText("Se connecter")).toHaveCount(0);
  await expectNoA11yViolations(pub);
  await anon.close();
});

test("formulaire public : durée de disponibilité et nombre d'utilisations, désactivation automatique", async ({ page, browser }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `form-${id}`;
  expect(await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [], nodes: [{ id: "a", type: "wait", name: "Pause", seconds: 1 }] })).toBe(201);
  await page.goto("/triggers");
  await page.getByRole("tab", { name: "Formulaires publics" }).click();
  await page.getByRole("button", { name: "Nouveau formulaire public" }).click();
  await page.getByLabel("Nom interne").fill(`Limité ${id}`);
  await page.getByLabel("Titre affiché").fill(`Une seule fois ${id}`);
  await page.getByLabel("Workflow", { exact: true }).selectOption({ label: `Test ${slug}` });
  await page.getByLabel("Nombre d'utilisations maximum (facultatif)").fill("1");
  const end = new Date(Date.now() + 7 * 86400_000).toISOString().slice(0, 16);
  await page.getByLabel("Disponible jusqu'au (facultatif)").fill(end);
  await expectNoA11yViolations(page);
  await page.getByRole("button", { name: "Créer le formulaire" }).click();
  const link = (await page.getByRole("dialog").getByText(/\/f\//).first().textContent()) ?? "";
  const url = link.slice(link.indexOf("http"));
  await page.getByRole("button", { name: "Fermer", exact: true }).last().click();
  await page.getByLabel("Rechercher un formulaire").fill(`Limité ${id}`); // la liste est paginée
  const row = page.getByRole("row", { name: new RegExp(`Limité ${id}`) });
  await expect(row).toContainText("Disponible");
  await expect(row).toContainText("0 / 1 utilisation(s)");

  const anon = await browser.newContext();
  const pub = await anon.newPage();
  await pub.goto(url);
  await pub.getByRole("button", { name: "Envoyer" }).click();
  await expect(pub.getByText("Merci")).toBeVisible();
  await pub.goto(url); // la dernière utilisation est passée : le formulaire s'est désactivé
  await expect(pub.getByRole("heading", { name: "Formulaire introuvable" })).toBeVisible();
  await anon.close();

  await page.reload();
  await page.getByRole("tab", { name: "Formulaires publics" }).click();
  await page.getByLabel("Rechercher un formulaire").fill(`Limité ${id}`);
  await expect(row).toContainText("Limite d'utilisations atteinte");
  await expect(row).toContainText("1 / 1 utilisation(s)");
  // on le rouvre en remettant le compteur à zéro
  await row.getByRole("button", { name: `Modifier la disponibilité de Limité ${id}` }).click();
  const dlg = page.getByRole("dialog", { name: new RegExp(`Disponibilité de « Limité ${id} »`) });
  await dlg.getByLabel("Remettre le compteur d'utilisations à zéro").check();
  await expectNoA11yViolations(page);
  await dlg.getByRole("button", { name: "Enregistrer" }).click();
  await expect(row).toContainText("Disponible");
  await expect(row).toContainText("0 / 1 utilisation(s)");
});
