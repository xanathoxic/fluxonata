import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, goNav, login, publishViaApi } from "./helpers";

const uniq = () => `${Date.now()}`.slice(-7);

test("un workflow est proposé au catalogue, trouvé, retiré ; un lecteur ne peut pas le proposer", async ({ page, browser }) => {
  const id = uniq();
  const slug = `cat-${id}`;
  const graph = {
    inputs: { type: "object", required: ["text"], properties: { text: { type: "string", title: "Texte à analyser" } } }, outputs: {}, edges: [],
    nodes: [{ id: "a", type: "action", name: "Analyser", action: "text-stats", inputs: { text: "{{ inputs.text }}" } }],
  };
  await login(page, "alice");
  expect(await publishViaApi(page, slug, graph)).toBe(201);

  // pas au catalogue tant que personne ne l'a décidé
  await goNav(page, "Catalogue");
  await page.getByLabel("Rechercher une automatisation").fill(slug);
  await expect(page.getByText("Aucune automatisation ne correspond")).toBeVisible();

  await page.goto(`/workflows/${slug}/schema`);
  await page.getByRole("button", { name: "Publier au catalogue" }).click();
  await expect(page.getByRole("button", { name: "Retirer du catalogue" })).toHaveAttribute("aria-pressed", "true");

  await goNav(page, "Catalogue");
  await expect(page.getByRole("heading", { name: "Catalogue d'automatisations" })).toBeVisible();
  await expectNoA11yViolations(page);
  await page.getByLabel("Rechercher une automatisation").fill(slug);
  await page.getByRole("link", { name: `Test ${slug}` }).click();
  await expect(page.getByRole("heading", { name: "Informations à fournir" })).toBeVisible();
  await expect(page.getByText("Texte à analyser")).toBeVisible();
  await expectNoA11yViolations(page);
  await page.getByRole("link", { name: "Lancer", exact: true }).click();
  await expect(page.getByLabel(/Texte à analyser/)).toBeVisible();

  // un lecteur le voit mais ne peut ni le proposer ni le retirer
  const reader = await (await browser.newContext()).newPage();
  await login(reader, "bob");
  await goNav(reader, "Catalogue");
  await reader.getByLabel("Rechercher une automatisation").fill(slug);
  await expect(reader.getByRole("link", { name: `Test ${slug}` })).toBeVisible();
  await reader.goto(`/workflows/${slug}/schema`);
  await expect(reader.getByRole("button", { name: /catalogue/i })).toHaveCount(0);

  // retiré : il disparaît
  await page.goto(`/workflows/${slug}/schema`);
  await page.getByRole("button", { name: "Retirer du catalogue" }).click();
  await expect(page.getByRole("button", { name: "Publier au catalogue" })).toBeVisible();
  await reader.reload();
  await goNav(reader, "Catalogue");
  await reader.getByLabel("Rechercher une automatisation").fill(slug);
  await expect(reader.getByText("Aucune automatisation ne correspond")).toBeVisible();
  const status = await reader.evaluate(async (s) => (await fetch(`/api/v1/workspaces/demo/catalog/${s}/`, { credentials: "same-origin" })).status, slug);
  expect(status).toBe(404);
});

test("un workflow jamais publié n'a pas de bouton « Publier au catalogue »", async ({ page }) => {
  await login(page, "alice");
  await goNav(page, "Workflows");
  await page.getByRole("button", { name: "Nouveau workflow" }).click();
  await page.getByLabel("Nom du nouveau workflow").fill(`Brouillon ${uniq()}`);
  await page.getByRole("button", { name: "Créer et ouvrir l'éditeur" }).click();
  await expect(page.getByRole("toolbar", { name: "Actions du workflow" })).toBeVisible();
  await expect(page.getByRole("button", { name: /catalogue/i })).toHaveCount(0);
});
