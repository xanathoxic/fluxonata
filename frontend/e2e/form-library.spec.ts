import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, login, publishViaApi } from "./helpers";

const nav = (page: Page, name: string) => page.getByRole("navigation", { name: "Navigation principale" }).getByRole("link", { name, exact: true }).click();

test("formulaires : créer depuis les entrées d'un workflow, concevoir, publier, rattacher à un déclencheur public", async ({ page, browser }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `fl-${id}`;
  const props = { sujet: { type: "string", title: "Sujet" }, motif: { type: "string", title: "Motif" }, detail: { type: "string", title: "Détail" } };
  expect(await publishViaApi(page, slug, { inputs: { type: "object", properties: props, required: ["sujet"] }, outputs: {}, edges: [], nodes: [{ id: "a", type: "wait", name: "Pause", seconds: 1 }] })).toBe(201);

  // l'entrée « Formulaires » est rangée sous « Bibliothèques » dans le menu de gauche
  await nav(page, "Bibliothèques");
  await page.getByRole("list", { name: "Bibliothèques" }).getByRole("link", { name: "Formulaires" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Formulaires" })).toBeVisible();
  await expectNoA11yViolations(page);
  await page.getByRole("button", { name: "Nouveau formulaire" }).click();
  await page.getByLabel("Nom du formulaire").fill(`Demande ${id}`);
  await page.getByLabel("Les entrées d'un workflow").check();
  await page.getByLabel("Workflow", { exact: true }).selectOption({ label: `Test ${slug}` });
  await page.getByRole("button", { name: "Créer et ouvrir le concepteur" }).click();

  // conception : un champ par entrée, une liste et une règle d'affichage
  await expect(page.getByRole("heading", { level: 1 })).toContainText(`Demande ${id}`);
  await expect(page.getByRole("button", { name: /^Sujet/ })).toBeVisible();
  await page.getByRole("button", { name: /^Motif/ }).click();
  await page.getByLabel("Type de champ").selectOption("select");
  await page.getByLabel("Une option par ligne : valeur | libellé").fill("panne | Panne\nautre | Autre");
  await page.getByRole("button", { name: /^Détail/ }).click();
  // les conditions se gèrent dans une fenêtre au milieu de l'écran
  await page.getByRole("button", { name: "Ajouter des conditions" }).click();
  const dlg = page.getByRole("dialog", { name: /Conditions du champ/ });
  await dlg.getByRole("button", { name: "Afficher seulement si…" }).click();
  await dlg.getByLabel("Condition 1 : champ").selectOption({ label: "Motif" });
  await dlg.getByLabel("Condition 1 : valeur").selectOption("autre");
  await dlg.getByRole("button", { name: "Appliquer" }).click();
  await expectNoA11yViolations(page);
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 }); // enregistrement automatique

  // publication d'une version immuable
  await page.getByRole("button", { name: "Publier", exact: true }).click();
  await page.getByLabel("Notes de version (facultatif)").fill("première version");
  await page.getByRole("dialog").getByRole("button", { name: "Publier" }).click();
  await expect(page.getByText("v1.0.0 publiée")).toBeVisible();
  await page.getByRole("tab", { name: "Versions" }).click();
  await expect(page.getByRole("row", { name: /v1\.0\.0/ })).toContainText("première version");

  // rattachement à un formulaire public : la page publique sert exactement cette version
  await page.goto("/triggers");
  await page.getByRole("tab", { name: "Formulaires publics" }).click();
  await page.getByRole("button", { name: "Nouveau formulaire public" }).click();
  await page.getByLabel("Nom interne").fill(`Public ${id}`);
  await page.getByLabel("Titre affiché").fill(`Titre ${id}`);
  await page.getByLabel("Workflow", { exact: true }).selectOption({ label: `Test ${slug}` });
  await page.getByRole("button", { name: "Créer le formulaire" }).click();
  const link = (await page.getByRole("dialog").getByText(/\/f\//).first().textContent()) ?? "";
  const url = link.slice(link.indexOf("http"));
  await page.getByRole("button", { name: "Fermer", exact: true }).last().click();
  await page.getByLabel("Rechercher un formulaire").fill(`Public ${id}`);
  await page.getByRole("button", { name: `Choisir le formulaire de Public ${id}` }).click();
  await page.getByLabel("Formulaire", { exact: true }).selectOption({ label: `Demande ${id}` });
  await page.getByRole("button", { name: "Rattacher" }).click();
  await expect(page.getByRole("row", { name: new RegExp(`Public ${id}`) })).toContainText("v1.0.0");

  const anon = await browser.newContext();
  const pub = await anon.newPage();
  await pub.goto(url);
  await expect(pub.getByLabel("Détail")).toHaveCount(0);
  await expectNoA11yViolations(pub);
  await pub.getByRole("button", { name: "Envoyer" }).click();
  await expect(pub.getByRole("alert").filter({ hasText: "obligatoire" })).toBeVisible();
  await pub.getByLabel(/Sujet/).fill("Imprimante");
  await pub.getByLabel("Motif").selectOption("autre");
  await pub.getByLabel("Détail").fill("Plus de papier");
  await pub.getByRole("button", { name: "Envoyer" }).click();
  await expect(pub.getByText("Merci")).toBeVisible();
  await anon.close();

  // utilisation : le formulaire sait où il sert, et ne peut plus être supprimé
  await page.goto(`/forms/${slug}`);
  await page.goto("/forms");
  await page.getByLabel("Rechercher un formulaire").fill(`Demande ${id}`);
  await page.getByRole("link", { name: new RegExp(`Demande ${id}`) }).first().click();
  await page.getByRole("tab", { name: "Utilisation" }).click();
  await expect(page.getByText(`Public ${id}`)).toBeVisible();
});

test("formulaires : partir d'un modèle fourni, l'enregistrer comme modèle de l'espace, le retrouver", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  await page.goto("/forms");
  await page.getByRole("button", { name: "Nouveau formulaire" }).click();
  await page.getByLabel("Nom du formulaire").fill(`Incident ${id}`);
  await page.getByLabel("Un modèle", { exact: true }).check();
  await page.getByLabel("Modèle", { exact: true }).selectOption({ label: "Déclaration d'incident — Gravité, description et, si critique, impact : le champ d'impact n'apparaît que pour une gravité critique." });
  await page.getByRole("button", { name: "Créer et ouvrir le concepteur" }).click();
  await expect(page.getByRole("button", { name: /^Gravité/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Impact sur les utilisateurs/ })).toContainText("conditionnel");

  await page.getByRole("button", { name: "Plus d'actions" }).click();
  await page.getByRole("menuitem", { name: "Enregistrer comme modèle" }).click();
  await page.getByLabel("Nom", { exact: true }).fill(`Modèle incident ${id}`);
  await page.getByRole("button", { name: "Créer le modèle" }).click();
  await expect(page.getByText("Modèle", { exact: true }).first()).toBeVisible();
  await page.goto("/forms");
  await page.getByRole("tab", { name: "Modèles" }).click();
  await page.getByLabel("Rechercher un formulaire").fill(`Modèle incident ${id}`);
  await expect(page.getByRole("link", { name: `Modèle incident ${id}`, exact: true })).toBeVisible();
  await expectNoA11yViolations(page);
});
