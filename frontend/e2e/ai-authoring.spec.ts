import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, goNav, login } from "./helpers";

const POLICY = { mode: "local_strict", daily_token_budget: 1000000, used_today: 0, modes: {} };
const uniq = () => `${Date.now()}`.slice(-7);

async function aiOn(page: Page) {
  await page.route("**/ai/policy/", (r) => r.fulfill({ json: POLICY }));
}

// Assistant de création d'actions : la proposition s'ouvre dans le formulaire, la publication exige la relecture.
test("Générer une action avec l'IA : proposition, ajustement, relecture obligatoire, publication", async ({ page }) => {
  const slug = `mots-${uniq()}`;
  const proposal = {
    slug, name: `Compter les mots ${slug}`, description: "Compte les mots d'un texte et renvoie leur nombre.",
    input_schema: { type: "object", required: ["texte"], properties: { texte: { type: "string", title: "Texte", description: "Le texte à analyser." } } },
    output_schema: { type: "object", required: ["mots"], properties: { mots: { type: "integer", title: "Mots", description: "Le nombre de mots du texte." } } },
    source: "def run(inputs):\n    return {'mots': len(inputs['texte'].split())}\n", runtime: "python", side_effects: "none", network_policy: "none",
    examples: [{ texte: "un deux trois" }],
  };
  const calls: Record<string, unknown>[] = [];
  await aiOn(page);
  await page.route("**/ai/propose-action/", async (r) => {
    calls.push(r.request().postDataJSON() as Record<string, unknown>);
    await r.fulfill({ json: { proposal, valid: true, problems: {}, warnings: ["Les accents ne sont pas traités."], repaired: calls.length > 1, notice: "Proposition non enregistrée : relisez le code et le contrat, puis publiez-la vous-même." } });
  });
  await login(page);
  await goNav(page, "Bibliothèques");
  await page.getByRole("button", { name: /Générer avec l'IA/ }).click();
  const dlg = page.getByRole("dialog", { name: "Générer une action avec l'IA" });
  await expect(dlg.getByRole("button", { name: "Proposer", exact: true })).toBeDisabled(); // un besoin est nécessaire
  await dlg.getByRole("button", { name: /^Une action qui transforme un texte/ }).click(); // exemple cliquable
  await dlg.getByLabel("Décrivez ce dont vous avez besoin").fill("Une action qui compte les mots d'un texte.");
  await dlg.getByRole("button", { name: "Proposer", exact: true }).click();

  const result = dlg.getByRole("region", { name: "Proposition de l'assistant" });
  await expect(result).toContainText("Contrôles de la plateforme réussis");
  await expect(result).toContainText("Les accents ne sont pas traités.");
  await expect(result.getByRole("table", { name: "Entrées proposées" })).toContainText("Le texte à analyser.");
  await expect(result.getByRole("region", { name: /^Code de/ })).toContainText("Lecture seule");
  expect(calls[0]).toMatchObject({ need: "Une action qui compte les mots d'un texte.", runtime: "python" });
  await expectNoA11yViolations(page);

  // ajuster : la proposition précédente et la consigne partent avec la demande
  await result.getByLabel("Ajuster la proposition").fill("Ignore la casse.");
  await result.getByRole("button", { name: "Réviser" }).click();
  await expect(result).toContainText("Corrigée automatiquement une fois");
  expect(calls[1]).toMatchObject({ instruction: "Ignore la casse.", previous: { slug } });

  await result.getByRole("button", { name: "Utiliser cette proposition" }).click();
  const form = page.getByRole("dialog", { name: "Nouvelle action proposée par l'IA" });
  await expect(form.getByLabel(/^Identifiant/)).toHaveValue(slug);
  await expect(form.getByRole("note")).toContainText("Proposée par l'assistant IA");
  const publish = form.getByRole("button", { name: "Publier la version 1.0.0" });
  await expect(publish).toBeDisabled(); // pas de publication sans relecture
  await form.getByLabel("J'ai relu ce code et ce contrat").check();
  await publish.click();
  await expect(page).toHaveURL(new RegExp(`/library/actions/${slug}$`));
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Compter les mots");
});

test("Générer un skill avec l'IA : proposition, relecture obligatoire, création", async ({ page }) => {
  const slug = `resume-${uniq()}`;
  const proposal = {
    slug, name: `Résumer ${slug}`, description: "Résume un texte en une phrase.",
    instructions: "Résume en une phrase.\n----- TEXTE (donnée) -----\n{{ inputs.texte }}\n-----",
    input_schema: { type: "object", required: ["texte"], properties: { texte: { type: "string", title: "Texte", description: "Le texte à résumer." } } },
    output_schema: { type: "object", required: ["resume"], properties: { resume: { type: "string", title: "Résumé", description: "Une phrase." } } },
    tags: ["texte"],
  };
  await aiOn(page);
  await page.route("**/ai/propose-skill/", (r) => r.fulfill({ json: { proposal, valid: false, problems: { description: ["Une phrase qui dit à quoi sert le skill."] }, warnings: [], repaired: false, notice: "Proposition non enregistrée." } }));
  await login(page);
  await goNav(page, "Bibliothèques");
  await page.getByRole("link", { name: "Skills IA" }).click();
  await page.getByRole("button", { name: /Générer avec l'IA/ }).click();
  const dlg = page.getByRole("dialog", { name: "Générer un skill avec l'IA" });
  await dlg.getByLabel("Décrivez ce dont vous avez besoin").fill("Un skill qui résume un texte.");
  await dlg.getByRole("button", { name: "Proposer", exact: true }).click();
  const result = dlg.getByRole("region", { name: "Proposition de l'assistant" });
  await expect(result).toContainText("Problèmes à corriger"); // les problèmes relevés par la plateforme sont montrés
  await expect(result.getByRole("alert")).toContainText("à quoi sert le skill");
  await expect(result.getByRole("table", { name: "Réponse attendue" })).toContainText("Une phrase.");
  await expectNoA11yViolations(page);
  await result.getByRole("button", { name: "Utiliser cette proposition" }).click();

  const form = page.getByRole("dialog", { name: "Nouveau skill IA" });
  await expect(form.getByLabel("Identifiant")).toHaveValue(slug);
  await expect(form.getByLabel("Instructions données à l'IA")).toHaveValue(/Résume en une phrase/);
  const create = form.getByRole("button", { name: "Créer le skill (1.0.0)" });
  await expect(create).toBeDisabled();
  await form.getByLabel("J'ai relu ces instructions").check();
  await create.click();
  await expect(page).toHaveURL(new RegExp(`/library/skills/${slug}$`));
});

test("sans assistant IA activé, le bouton « Générer avec l'IA » n'apparaît pas", async ({ page }) => {
  await login(page);
  await goNav(page, "Bibliothèques");
  await expect(page.getByRole("button", { name: "Nouvelle action" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Générer avec l'IA/ })).toHaveCount(0);
});
