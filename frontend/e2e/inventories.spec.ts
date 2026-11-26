import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, login } from "./helpers";

test("inventaires Ansible : créer, éditer machines et groupes, importer, enregistrer et retrouver", async ({ page }) => {
  await login(page);
  const slug = `inv-${Date.now().toString(36)}`;
  const read = () => page.evaluate(async (s) => await (await fetch(`/api/v1/workspaces/demo/inventories/${s}/`)).json(), slug);

  // création depuis la liste (l'identifiant se déduit du nom)
  await page.goto("/library/inventories");
  await expect(page.getByRole("heading", { name: "Inventaires" })).toBeVisible();
  await expectNoA11yViolations(page);
  await page.getByRole("button", { name: /Nouvel inventaire/ }).click();
  await page.getByLabel("Nom de l'inventaire").fill(slug);
  await page.getByRole("button", { name: "Créer" }).click();
  await expect(page).toHaveURL(new RegExp(`/library/inventories/${slug}$`));

  // un groupe, une machine dans ce groupe, des variables
  await page.getByRole("button", { name: /Ajouter un groupe/ }).click();
  await page.getByLabel("Nom du groupe 1").fill("web");
  await page.getByLabel("Nom du groupe 1").blur();
  await page.getByRole("button", { name: /Ajouter une machine/ }).click();
  await page.getByLabel("Nom de la machine 1").fill("web1.exemple.fr");
  await page.getByLabel("Nom de la machine 1").blur();
  await page.locator("details.inv-members summary").last().click();
  await page.getByRole("checkbox", { name: "web", exact: true }).check();
  await page.getByLabel("Variables de web1.exemple.fr (JSON)").fill('{"ansible_host":"10.0.0.5"}');
  await page.getByLabel("Variables communes (JSON)").fill('{"env":"prod"}');
  await expectNoA11yViolations(page);

  // une variable qui porte un identifiant est refusée par le serveur, avec la raison
  await page.getByLabel("Variables de web1.exemple.fr (JSON)").fill('{"ansible_password":"x"}');
  await page.getByRole("button", { name: "Enregistrer" }).click();
  await expect(page.getByRole("alert").filter({ hasText: /identifiant/ })).toBeVisible();
  await page.getByLabel("Variables de web1.exemple.fr (JSON)").fill('{"ansible_host":"10.0.0.5"}');
  await page.getByRole("button", { name: "Enregistrer" }).click();
  await expect(page.getByText("Modifications non enregistrées.")).toHaveCount(0);
  await expect
    .poll(async () => (await read()).hosts.map((h: { name: string; groups: string[] }) => `${h.name}:${h.groups.join(",")}`))
    .toEqual(["web1.exemple.fr:web"]);
  expect((await read()).variables).toEqual({ env: "prod" });

  // aperçu : ce que recevra Ansible
  await page.getByRole("button", { name: "Aperçu Ansible" }).click();
  await expect(page.getByRole("region", { name: "Inventaire au format JSON" })).toContainText(/"web1.exemple.fr"[\s\S]*"ansible_host": "10.0.0.5"/);
  await page.keyboard.press("Escape");

  // import YAML qui remplace l'édition, puis enregistrement
  await page.getByRole("button", { name: "Importer YAML/JSON" }).click();
  await page.getByLabel("Inventaire YAML ou JSON").fill("all:\n  children:\n    db:\n      hosts:\n        db1: {}\n        db2: {}\n");
  await page.getByRole("dialog").getByRole("button", { name: "Importer" }).click();
  await expect(page.getByLabel("Nom de la machine 1")).toHaveValue("db1");
  await page.getByRole("button", { name: "Enregistrer" }).click();
  await expect.poll(async () => (await read()).hosts.map((h: { name: string }) => h.name)).toEqual(["db1", "db2"]);

  // retrouvé après rechargement, dans la liste, puis supprimé
  await page.reload();
  await expect(page.getByLabel("Nom de la machine 2")).toHaveValue("db2");
  await page.goto("/library/inventories");
  await expect(page.getByRole("link", { name: slug, exact: true })).toBeVisible();
  await page.getByRole("button", { name: `Supprimer ${slug}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Supprimer" }).click();
  await expect(page.getByRole("link", { name: slug, exact: true })).toHaveCount(0);
});
