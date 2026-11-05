import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, login } from "./helpers";

/** Enregistrement automatique des éditeurs (contrôle et formulaire) : enregistré tout seul, conflit, reprise locale, publication après modification. */

async function call(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ({ method, path, body }) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    const r = await fetch(`/api/v1/workspaces/demo${path}`, { method, headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: body ? JSON.stringify(body) : undefined });
    return { status: r.status, data: await r.json().catch(() => ({})) };
  }, { method, path, body });
}
const spec = { version: 1, mode: "group", parts: [{ key: "nom", label: "Nom", type: "text" }] };
const status = (page: Page) => page.getByRole("status").filter({ hasText: /Brouillon|Modifications|Enregistrement|Conflit|Non enregistré/ }).first();
const section = (page: Page, name: string) => page.getByRole("navigation", { name: "Sections du contrôle" }).getByRole("button", { name }).click();

test("contrôle : une modification est enregistrée toute seule et retrouvée au rechargement, sans bouton Enregistrer", async ({ page }) => {
  await login(page);
  const slug = `as-${Date.now().toString().slice(-6)}`;
  await call(page, "POST", "/control-library/", { slug, name: `Auto ${slug}`, spec });
  await page.goto(`/library/controls/${slug}/design`);
  await expect(page.getByRole("button", { name: "Enregistrer", exact: true })).toHaveCount(0);
  await section(page, "Général");
  await page.getByLabel("Description").fill("décrit tout seul");
  await expect(status(page)).toContainText(/Modifications en attente|Enregistrement/);
  await expect(status(page)).toContainText("Brouillon enregistré à", { timeout: 15_000 });
  await page.reload();
  await section(page, "Général");
  await expect(page.getByLabel("Description")).toHaveValue("décrit tout seul");
  expect((await call(page, "GET", `/control-library/${slug}/`)).data.description).toBe("décrit tout seul");
});

test("contrôle : modifié ailleurs → conflit proposé, rien d'écrasé sans choix ; « Écraser » garde ma version", async ({ page }) => {
  await login(page);
  const slug = `as-${Date.now().toString().slice(-6)}`;
  await call(page, "POST", "/control-library/", { slug, name: `Conflit ${slug}`, spec });
  await page.goto(`/library/controls/${slug}/design`);
  await section(page, "Général");
  // quelqu'un d'autre enregistre le brouillon entre-temps
  const cur = (await call(page, "GET", `/control-library/${slug}/`)).data;
  const other = await call(page, "PATCH", `/control-library/${slug}/`, { description: "version de l'autre", spec, revision: cur.draft_revision });
  expect(other.status).toBe(200);
  await page.getByLabel("Description").fill("ma version");
  const alert = page.getByRole("alert").filter({ hasText: "modifié ailleurs" });
  await expect(alert).toBeVisible({ timeout: 15_000 });
  await expect(status(page)).toContainText("Conflit");
  expect((await call(page, "GET", `/control-library/${slug}/`)).data.description).toBe("version de l'autre"); // pas écrasé
  await alert.getByRole("button", { name: "Écraser avec ma version" }).click();
  await expect(status(page)).toContainText("Brouillon enregistré", { timeout: 15_000 });
  expect((await call(page, "GET", `/control-library/${slug}/`)).data.description).toBe("ma version");
});

test("contrôle : une copie locale non enregistrée est proposée à la reprise", async ({ page }) => {
  await login(page);
  const slug = `as-${Date.now().toString().slice(-6)}`;
  await call(page, "POST", "/control-library/", { slug, name: `Reprise ${slug}`, spec });
  // le serveur est injoignable pour l'enregistrement : la modification reste dans le navigateur
  await page.route(`**/control-library/${slug}/`, (route) => (route.request().method() === "PATCH" ? route.abort() : route.continue()));
  await page.goto(`/library/controls/${slug}/design`);
  await section(page, "Général");
  await page.getByLabel("Description").fill("jamais envoyée");
  await expect(status(page)).toContainText("Enregistrement impossible", { timeout: 15_000 });
  await page.unroute(`**/control-library/${slug}/`);
  await page.evaluate(() => { window.onbeforeunload = null; });
  await page.goto(`/library/controls/${slug}/design`);
  const found = page.getByRole("alert").filter({ hasText: "retrouvées dans ce navigateur" });
  await expect(found).toBeVisible();
  await found.getByRole("button", { name: "Les récupérer" }).click();
  await section(page, "Général");
  await expect(page.getByLabel("Description")).toHaveValue("jamais envoyée");
  await expect(status(page)).toContainText("Brouillon enregistré", { timeout: 15_000 });
});

test("formulaire : publier juste après une modification publie cette modification", async ({ page }) => {
  await login(page);
  const slug = `as-form-${Date.now().toString().slice(-6)}`;
  const def = { version: 1, sections: [{ id: "s1", title: "Demande", fields: [{ key: "sujet", label: "Sujet", type: "text", required: true }] }] };
  await call(page, "POST", "/form-library/", { slug, name: `Publier ${slug}`, definition: def });
  await page.goto(`/library/forms/${slug}/design`);
  await page.getByRole("button", { name: /^Sujet/ }).click();
  await page.getByLabel("Libellé", { exact: true }).fill("Sujet modifié");
  await page.getByRole("button", { name: "Publier" }).click(); // sans attendre l'enregistrement automatique
  await page.getByRole("dialog", { name: "Publier une version" }).getByRole("button", { name: "Publier" }).click();
  await expect(page.getByText("v1.0.0 publiée")).toBeVisible({ timeout: 15_000 });
  const v = await call(page, "GET", `/form-library/${slug}/versions/1.0.0/`);
  expect(JSON.stringify(v.data.definition)).toContain("Sujet modifié");
});

test("formulaire : la fenêtre de publication résume les changements et suggère le type de changement", async ({ page }) => {
  await login(page);
  const slug = `as-pub-${Date.now().toString().slice(-6)}`;
  const two = { version: 1, sections: [{ id: "s1", title: "Demande", fields: [{ key: "sujet", label: "Sujet", type: "text" }, { key: "motif", label: "Motif", type: "text" }] }] };
  await call(page, "POST", "/form-library/", { slug, name: `Résumé ${slug}`, definition: two });
  expect((await call(page, "POST", `/form-library/${slug}/publish/`, {})).status).toBe(201);
  const cur = (await call(page, "GET", `/form-library/${slug}/`)).data;
  const one = { ...two, sections: [{ ...two.sections[0]!, fields: [two.sections[0]!.fields[0]] }] };
  expect((await call(page, "PATCH", `/form-library/${slug}/`, { definition: one, revision: cur.draft_revision })).status).toBe(200);
  await page.goto(`/library/forms/${slug}/design`);
  await page.getByRole("button", { name: "Publier", exact: true }).click();
  const dlg = page.getByRole("dialog", { name: "Publier une version" });
  await expect(dlg.getByRole("region", { name: "Changements depuis la dernière version" })).toContainText("Retirés : Motif (motif)");
  await expect(dlg.getByRole("radio", { name: /Changement incompatible/ })).toBeChecked();
  await dlg.getByRole("button", { name: "Publier", exact: true }).click();
  await expect(page.getByText("v2.0.0 publiée")).toBeVisible({ timeout: 15_000 });
});

test("formulaire sur téléphone : message « édition recommandée sur grand écran » et aperçu seulement", async ({ page }) => {
  await login(page);
  const slug = `as-mob-${Date.now().toString().slice(-6)}`;
  await call(page, "POST", "/form-library/", { slug, name: `Mobile ${slug}`, definition: { version: 1, sections: [{ id: "s1", title: "Demande", fields: [{ key: "sujet", label: "Sujet", type: "text" }] }] } });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/library/forms/${slug}/design`);
  await expect(page.getByRole("note").filter({ hasText: "Édition recommandée sur grand écran" })).toBeVisible();
  await expect(page.getByRole("tablist", { name: "Vue de l'éditeur" }).getByRole("tab")).toHaveText(["Aperçu"]);
  await expect(page.getByLabel("Sujet")).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.getByRole("tablist", { name: "Vue de l'éditeur" }).getByRole("tab")).toHaveText(["Éditeur", "Apparence", "Aperçu"]);
});

test("éditeur de formulaire : sélectionner un champ ne décale pas la grille ; inspecteur en groupes repliables mémorisés", async ({ page }) => {
  await login(page);
  const slug = `as-insp-${Date.now().toString().slice(-6)}`;
  await call(page, "POST", "/form-library/", { slug, name: `Inspecteur ${slug}`, definition: { version: 1, sections: [{ id: "s1", title: "Demande", fields: [{ key: "sujet", label: "Sujet", type: "text" }, { key: "motif", label: "Motif", type: "text" }] }] } });
  await page.evaluate(() => { for (const k of ["content", "validation", "logic"]) localStorage.removeItem(`fluxonata.fieldprops.${k}`); });
  await page.goto(`/library/forms/${slug}/design`);
  const grid = page.getByRole("group", { name: "Grille de la section Demande" });
  await expect(grid).toBeVisible();
  const y0 = (await grid.boundingBox())!.y;
  await page.getByRole("button", { name: /^Sujet/ }).click();
  await expect(page.getByLabel("Libellé", { exact: true })).toBeVisible();
  expect((await grid.boundingBox())!.y).toBe(y0); // la barre d'outils garde sa place : rien ne bouge
  const panel = page.getByRole("complementary", { name: "Propriétés" });
  for (const g of ["Contenu", "Validation", "Logique"]) await expect(panel.locator("summary", { hasText: g })).toBeVisible();
  await expect(panel.getByRole("button", { name: /conditions/i })).toBeVisible();
  // Déplacer / Redimensionner / Supprimer sont trois groupes distincts
  for (const g of ["Déplacer", "Redimensionner", "Supprimer"]) await expect(page.getByRole("group", { name: g, exact: true }).first()).toBeVisible();
  await panel.locator("summary", { hasText: "Validation" }).click();
  await expect(panel.getByLabel("Obligatoire")).toBeHidden();
  await page.reload();
  await page.getByRole("button", { name: /^Sujet/ }).click();
  await expect(panel.getByLabel("Obligatoire")).toBeHidden(); // l'état replié est mémorisé
  await panel.locator("summary", { hasText: "Validation" }).click();
  await expect(panel.getByLabel("Obligatoire")).toBeVisible();
  await expectNoA11yViolations(page);
});

test("contrôle : « Aperçu & test » avec mode développeur (JSON masqué par défaut), colonne Utilisation, ponctuation correcte", async ({ page }) => {
  await login(page);
  const slug = `as-ctl-${Date.now().toString().slice(-6)}`;
  await call(page, "POST", "/control-library/", { slug, name: `Fusion ${slug}`, spec });
  expect((await call(page, "POST", `/control-library/${slug}/publish/`, {})).status).toBe(201);
  const made = await call(page, "POST", "/form-library/", { slug: `f-${slug}`, name: `Utilise ${slug}`, definition: { version: 1, sections: [{ id: "s1", title: "S", fields: [{ key: "c", label: "C", type: "custom", control: { ref: slug, version: "1.0.0", name: "Fusion", spec } }] }] } });
  expect(made.status, JSON.stringify(made.data)).toBe(201);
  await page.goto(`/library/controls/${slug}/design`);
  await expect(page.getByText("Valeur remise au workflow : un objet.")).toBeVisible(); // pas d'espace avant le point
  const tabs = page.getByRole("tablist", { name: "Contrôle" }).getByRole("tab");
  await expect(tabs).toHaveText([/^Conception/, /^Aperçu & test$/, /^Versions/, /^Utilisation/]);
  await tabs.nth(1).click();
  await expect(page.getByRole("region", { name: "Aperçu du contrôle" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Test du contrôle" })).toHaveCount(0); // le JSON est réservé au mode développeur
  await page.getByRole("checkbox", { name: "Mode développeur" }).check();
  await expect(page.getByRole("region", { name: "Test du contrôle" })).toBeVisible();
  await expect(page.getByText("Options envoyées")).toHaveCount(0); // un groupe n'a pas d'options
  await page.getByRole("checkbox", { name: "Mode développeur" }).uncheck();
  await expect(page.getByRole("region", { name: "Test du contrôle" })).toHaveCount(0);
  await page.goto("/library/controls");
  await expect(page.getByRole("row", { name: new RegExp(`Fusion ${slug}`) })).toContainText("1 formulaire");
});
