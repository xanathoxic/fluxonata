import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, goNav, login, publishViaApi } from "./helpers";

const uniq = () => `${Date.now()}`.slice(-7);
const subnav = (page: Page, name: string) => page.getByRole("list", { name: "Bibliothèques" }).getByRole("link", { name, exact: true });

async function createSkillViaApi(page: Page, slug: string) {
  return page.evaluate(async (slug) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    const r = await fetch("/api/v1/workspaces/demo/ai/skills/", {
      method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify({ slug, name: `Skill ${slug}`, description: "Skill de test", folder: "Tests", version: {
        instructions: "Résume : {{ inputs.texte }}", input_schema: { type: "object", required: ["texte"], properties: { texte: { type: "string", description: "Le texte" } } },
        output_schema: { type: "object", required: ["resume"], properties: { resume: { type: "string" } } } } }),
    });
    return r.status;
  }, slug);
}

test("Bibliothèques : sous-menus Actions et Skills IA ; ranger une action dans un dossier (même arbre que les workflows)", async ({ page }) => {
  const id = uniq();
  await login(page);
  await goNav(page, "Bibliothèques");
  await expect(page).toHaveURL(/\/library\/actions$/);
  await expect(subnav(page, "Actions")).toHaveAttribute("aria-current", "page");
  await expect(subnav(page, "Skills IA")).toBeVisible();
  await expectNoA11yViolations(page);

  const folder = `Rangement ${id}/Sous ${id}`;
  await page.getByLabel("Rechercher une action").fill("Statistiques de texte"); // la liste est paginée : on cherche
  await page.getByRole("button", { name: /Déplacer l'action Statistiques de texte/ }).click();
  const dlg = page.getByRole("dialog", { name: /Déplacer « Statistiques de texte »/ });
  await dlg.getByLabel("Dossier").fill(folder);
  await expectNoA11yViolations(page);
  await dlg.getByRole("button", { name: "Déplacer", exact: true }).click();
  const tree = page.getByRole("navigation", { name: "Dossiers d'actions" });
  await expect(tree.getByRole("button", { name: new RegExp(`Rangement ${id}`) })).toBeVisible();
  await tree.getByRole("button", { name: new RegExp(`Sous ${id}`) }).click();
  const rows = page.getByRole("table").getByRole("row");
  await expect(rows.filter({ hasText: "Statistiques de texte" })).toContainText(folder);
  await expect(rows.filter({ hasText: "Attente simulée" })).toHaveCount(0); // filtré par dossier
  // état final propre : retour à la racine
  await page.getByRole("button", { name: /Déplacer l'action Statistiques de texte/ }).click();
  await page.getByRole("dialog").getByLabel("Dossier").fill("");
  await page.getByRole("dialog").getByRole("button", { name: "Déplacer", exact: true }).click();
  await expect(tree.getByRole("button", { name: new RegExp(`^Rangement ${id} \\(`) })).toHaveCount(0);
});

test("Skills IA : créer, versionner, déplacer, déprécier (concepteur)", async ({ page }) => {
  const id = uniq();
  await login(page);
  await goNav(page, "Bibliothèques");
  await subnav(page, "Skills IA").click();
  await expect(page.getByRole("heading", { level: 1, name: "Skills IA" })).toBeVisible();
  await expect(page.getByRole("note")).toContainText("L'IA est désactivée"); // gouvernance : rappelé, jamais contourné
  await expectNoA11yViolations(page);

  await page.getByRole("button", { name: "Nouveau skill" }).click();
  const dlg = page.getByRole("dialog", { name: "Nouveau skill IA" });
  await dlg.getByLabel("Nom", { exact: true }).fill(`Qualifier ${id}`);
  await dlg.getByLabel("Dossier (facultatif)").fill("Texte/Analyse");
  await expect(dlg.getByLabel("Identifiant")).toHaveValue(`qualifier-${id}`);
  await expectNoA11yViolations(page);
  // une variable inconnue est refusée avec un message clair
  await dlg.getByLabel("Instructions données à l'IA").fill("Bonjour {{ inputs.inconnue }}");
  await dlg.getByRole("button", { name: /Créer le skill/ }).click();
  await expect(dlg.getByRole("alert")).toContainText("Variable inconnue");
  await dlg.getByLabel("Instructions données à l'IA").fill("Qualifie : {{ inputs.texte }}");
  await dlg.getByRole("button", { name: /Créer le skill/ }).click();

  await expect(page).toHaveURL(new RegExp(`/library/skills/qualifier-${id}$`));
  await expect(page.getByRole("heading", { level: 1, name: `Qualifier ${id}` })).toBeVisible();
  await expect(page.getByText("Qualifie : {{ inputs.texte }}")).toBeVisible();
  await expectNoA11yViolations(page);

  await page.getByRole("button", { name: "Nouvelle version" }).click();
  const v = page.getByRole("dialog", { name: /Nouvelle version/ });
  await v.getByLabel("Type de version").selectOption("patch");
  await v.getByLabel("Note de version").fill("Précision");
  await v.getByRole("button", { name: "Publier la version" }).click();
  await expect(page.getByRole("row", { name: /1\.0\.1.*Précision/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /1\.0\.0/ })).toBeVisible(); // l'ancienne version reste

  await page.getByRole("button", { name: "Déplacer", exact: true }).click();
  await page.getByRole("dialog").getByLabel("Dossier").fill("Texte/Autre");
  await page.getByRole("dialog").getByRole("button", { name: "Déplacer", exact: true }).click();
  await expect(page.getByText("Dossier : Texte/Autre")).toBeVisible();
  await page.getByRole("button", { name: "Déprécier" }).click();
  await expect(page.getByText("⚠ Déprécié").first()).toBeVisible();

  await page.getByRole("link", { name: "Skills IA", exact: true }).first().click();
  const tree = page.getByRole("navigation", { name: "Dossiers de skills" });
  await tree.getByRole("button", { name: /Autre/ }).click();
  await page.getByLabel("Rechercher un skill").fill(`Qualifier ${id}`);
  await expect(page.getByRole("link", { name: `Qualifier ${id}`, exact: true })).toBeVisible();
});

test("Étape IA d'un workflow : choisir un skill de la bibliothèque, ses entrées et son fournisseur", async ({ page }) => {
  const id = uniq();
  await login(page);
  expect(await createSkillViaApi(page, `etape-${id}`)).toBe(201);
  const slug = `ia-${id}`;
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [], nodes: [{ id: "a", type: "wait", name: "Pause", seconds: 1 }] });
  await page.goto(`/workflows/${slug}/schema`);
  await page.getByRole("button", { name: "Ajouter : Étape IA", exact: true }).click();
  await page.getByRole("tab", { name: "Étape IA", exact: true }).click();
  await expect(page.getByLabel("Utiliser un skill de la bibliothèque")).not.toBeChecked();
  await page.getByLabel("Utiliser un skill de la bibliothèque").check();
  await page.getByLabel("Skill", { exact: true }).selectOption(`etape-${id}`);
  await expect(page.getByText(/Version utilisée : la dernière publiée \(1\.0\.0\)/)).toBeVisible();
  await page.getByLabel(/^texte/).fill("Bonjour le monde");
  await expect(page.getByRole("button", { name: /problème\(s\) bloquant/ })).toHaveCount(0); // un skill n'exige ni tâche libre ni fournisseur propre
  await page.getByText("Voir les instructions du skill").click();
  await expect(page.getByLabel("Instructions du skill")).toContainText("Résume : {{ inputs.texte }}");
  await expectNoA11yViolations(page);
  await page.getByLabel("Une tâche libre").check(); // on peut revenir à une tâche libre
  await expect(page.getByLabel("Tâche confiée à l'IA")).toBeVisible();
});
