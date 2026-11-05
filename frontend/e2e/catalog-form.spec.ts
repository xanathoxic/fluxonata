import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, login, publishViaApi } from "./helpers";

const field = (key: string, label: string, extra: Record<string, unknown> = {}) => ({ key, label, type: "text", ...extra });

async function call(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ({ method, path, body }) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    const r = await fetch(`/api/v1/workspaces/demo${path}`, { method, headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: body ? JSON.stringify(body) : undefined });
    return { status: r.status, data: await r.json().catch(() => ({})) };
  }, { method, path, body });
}

async function offerInCatalog(page: Page, slug: string) {
  return page.evaluate(async (s) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    return (await fetch(`/api/v1/workspaces/demo/workflows/${s}/catalog/`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: "{}" })).status;
  }, slug);
}

test("catalogue : le formulaire conçu s'affiche (règle d'affichage), valide et lance l'automatisation", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `cf-${id}`;
  const props = { sujet: { type: "string", title: "Sujet" }, motif: { type: "string", title: "Motif" }, detail: { type: "string", title: "Détail" } };
  const input_form = { version: 1, sections: [{ id: "s1", title: "Votre demande", fields: [
    field("sujet", "Sujet", { required: true }),
    field("motif", "Motif", { type: "select", options: [{ value: "panne", label: "Panne" }, { value: "autre", label: "Autre" }] }),
    field("detail", "Détail", { visible_if: "values.motif == 'autre'" }),
  ] }] };
  const lib = await call(page, "POST", "/form-library/", { slug: `lib-${id}`, name: `Bibliothèque ${id}`, definition: input_form });
  expect(lib.status).toBe(201);
  expect((await call(page, "POST", `/form-library/lib-${id}/publish/`, {})).status).toBe(201);
  // le workflow référence le formulaire de la bibliothèque : la version est figée à la publication
  expect(await publishViaApi(page, slug, { inputs: { type: "object", properties: props, required: ["sujet"] }, input_form: { ref: { form: `lib-${id}`, version: null } }, outputs: {}, edges: [], nodes: [{ id: "a", type: "wait", name: "Pause", seconds: 1 }] })).toBe(201);
  expect(await offerInCatalog(page, slug)).toBeLessThan(300);

  await page.goto(`/catalog/${slug}`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(slug);
  await expect(page.getByRole("group", { name: "Votre demande" })).toBeVisible();
  await expect(page.getByLabel("Détail")).toHaveCount(0);
  await expectNoA11yViolations(page);
  await page.getByRole("button", { name: "Lancer" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "obligatoire" })).toBeVisible();
  await page.getByLabel(/Sujet/).fill("Imprimante");
  await page.getByLabel("Motif").selectOption("autre");
  await page.getByLabel("Détail").fill("Plus de papier");
  await page.getByRole("button", { name: "Lancer" }).click();
  await expect(page).toHaveURL(new RegExp(`/workflows/${slug}/follow\\?run=`));

  // la page de lancement d'un workflow affiche le même formulaire
  await page.goto(`/workflows/${slug}/run`);
  await expect(page.getByLabel(/Sujet/)).toBeVisible();
  await expect(page.getByLabel("Détail")).toHaveCount(0);
});

test("éditeur de workflow : onglet « Formulaire d'entrée » — choisir un formulaire de la bibliothèque ou le créer depuis les entrées", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `cd-${id}`;
  const props = { sujet: { type: "string", title: "Sujet" }, motif: { type: "string", title: "Motif" } };
  expect(await publishViaApi(page, slug, { inputs: { type: "object", properties: props, required: ["sujet"] }, outputs: {}, edges: [], nodes: [{ id: "a", type: "wait", name: "Pause", seconds: 1 }] })).toBe(201);
  await page.goto(`/workflows/${slug}/form`);
  await expect(page.getByRole("radio", { name: "Simple" })).toBeChecked();
  await page.getByRole("radio", { name: "Concepteur" }).check();
  await expect(page.getByRole("heading", { name: "Aucun formulaire choisi" })).toBeVisible();
  await page.getByRole("button", { name: "Créer à partir des entrées" }).click();
  await expect(page.getByRole("link", { name: "Ouvrir dans le concepteur" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Correspondance des entrées" })).toHaveCount(0); // l'onglet « Correspondance » est affiché d'abord
  await page.getByRole("tab", { name: "Aperçu" }).click();
  await expect(page.getByLabel(/Sujet/)).toBeVisible();
  await expect(page.getByLabel("Motif")).toBeVisible();
  await expectNoA11yViolations(page);

  // revenir au formulaire simple ne supprime rien : le formulaire reste dans la bibliothèque
  await page.getByRole("radio", { name: "Simple" }).click();
  await expect(page.getByRole("radio", { name: "Simple" })).toBeChecked();
  await page.goto("/forms");
  await page.getByLabel("Rechercher un formulaire").fill(`Formulaire de Test ${slug}`);
  await expect(page.getByRole("link", { name: `Formulaire de Test ${slug}`, exact: true })).toBeVisible();
});

test("correspondance des entrées : un formulaire aux champs nommés autrement remplit les entrées du workflow", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `map-${id}`;
  const props = { sujet: { type: "string", title: "Sujet" }, motif: { type: "string", title: "Motif" } };
  const inputs = { type: "object", properties: props, required: ["sujet"] };
  const nodes = [{ id: "a", type: "wait", name: "Pause", seconds: 1 }];
  const lib = `autres-${id}`;
  const def = { version: 1, sections: [{ id: "s1", title: "Votre demande", fields: [field("objet", "Objet", { required: true }), field("raison", "Raison"), field("remarque", "Remarque")] }] };
  expect((await call(page, "POST", "/form-library/", { slug: lib, name: `Autres noms ${id}`, definition: def })).status).toBe(201);
  expect((await call(page, "POST", `/form-library/${lib}/publish/`, {})).status).toBe(201);

  // sans correspondance, les noms ne concordent pas : la publication est refusée
  expect(await publishViaApi(page, `${slug}-x`, { inputs, input_form: { ref: { form: lib, version: null } }, outputs: {}, edges: [], nodes })).toBe(400);

  // dans l'éditeur : choisir le formulaire, puis les champs de chaque entrée
  expect(await publishViaApi(page, slug, { inputs, outputs: {}, edges: [], nodes })).toBe(201);
  await page.goto(`/workflows/${slug}/form`);
  await page.getByRole("radio", { name: "Concepteur" }).check();
  await page.getByRole("button", { name: "Choisir un formulaire" }).click();
  const dlg = page.getByRole("dialog", { name: "Formulaire d'entrée" });
  await dlg.getByLabel("Rechercher").fill(`Autres noms ${id}`);
  await dlg.getByRole("radio", { name: new RegExp(`Autres noms ${id}`) }).check();
  await dlg.getByRole("button", { name: "Utiliser ce formulaire" }).click();
  await expect(page.getByRole("heading", { name: `Autres noms ${id}` })).toBeVisible();
  const mapping = page.getByRole("tabpanel");
  await expect(page.getByText("Correspondance automatique")).toBeVisible();
  await expect(mapping.getByText("Entrée obligatoire : choisissez le champ qui la remplit.")).toBeVisible();
  await expect(page.getByText(/problème\(s\) de correspondance/)).toBeVisible();
  await mapping.getByRole("radio", { name: /Personnalisée/ }).check();
  await mapping.getByLabel("Champ pour l'entrée sujet").selectOption("objet");
  await mapping.getByLabel("Champ pour l'entrée motif").selectOption("raison");
  await expect(mapping.getByText("Entrée obligatoire")).toHaveCount(0);
  await expect(mapping.getByText("Champs qui ne remplissent aucune entrée (non transmis) : Remarque.")).toBeVisible();
  await expect(page.getByText("Correspondance valide")).toBeVisible();
  await expectNoA11yViolations(page);
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 30_000 });
  await page.reload();
  await expect(page.getByLabel("Champ pour l'entrée sujet")).toHaveValue("objet");

  // publié avec la correspondance : la page de lancement envoie bien les entrées du workflow
  const slug2 = `map2-${id}`;
  const map = { sujet: "objet", motif: "raison" };
  expect(await publishViaApi(page, slug2, { inputs, input_form: { ref: { form: lib, version: null }, map }, outputs: {}, edges: [], nodes })).toBe(201);
  await page.goto(`/workflows/${slug2}/run`);
  await page.getByLabel(/Objet/).fill("Imprimante");
  await page.getByLabel("Raison").fill("Panne");
  await page.getByLabel("Remarque").fill("non transmise");
  await page.getByRole("button", { name: "Lancer" }).click();
  await expect(page).toHaveURL(new RegExp(`/workflows/${slug2}/follow\\?run=`));
  const runId = new URL(page.url()).searchParams.get("run");
  const run = await call(page, "GET", `/workflow-runs/${runId}/`);
  expect(run.data.inputs).toEqual({ sujet: "Imprimante", motif: "Panne" }); // « Remarque » ne remplit aucune entrée : elle n'est pas transmise
});
