// Generates the illustrated user guide screenshots (fictional demo data). Run with scripts/screenshots.sh (SHOTS=1).
import { expect, test, type Page } from "@playwright/test";
import { goNav, login, menuClick } from "./helpers";

test.skip(!process.env.SHOTS, "set SHOTS=1 (scripts/screenshots.sh) to regenerate documentation screenshots");
const OUT = "../docs/img";
const nav = goNav;
const shot = async (page: Page, name: string, full = false) => { await page.waitForTimeout(400); await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: full }); };

test.use({ viewport: { width: 1280, height: 800 } });
test.setTimeout(240_000);

test("capture du guide utilisateur", async ({ browser }) => {
  const page = await (await browser.newContext()).newPage();
  await page.goto("/");
  await shot(page, "01-connexion");
  await login(page, "alice");
  await shot(page, "02-accueil");
  await nav(page, "Catalogue");
  await shot(page, "03-catalogue");
  await page.getByRole("link", { name: "Statistiques de texte" }).click();
  await expect(page.getByLabel(/Texte/)).toBeVisible(); // la saisie est sur la fiche elle-même
  await shot(page, "28-catalogue-fiche");
  await page.getByLabel(/Texte/).fill("Bonjour le monde en un clic");
  await shot(page, "04-formulaire-action");
  await page.getByRole("button", { name: "Lancer l'automatisation" }).click();
  await expect(page.getByText("L'exécution s'est terminée avec succès.")).toBeVisible({ timeout: 60_000 });
  await shot(page, "05-resultat", true);

  await nav(page, "Workflows");
  await shot(page, "06-workflows");
  await page.getByRole("link", { name: "Redémarrer des VM", exact: true }).click();
  await page.getByRole("navigation", { name: "Sections du workflow" }).getByRole("link", { name: "Conception", exact: true }).click();
  await shot(page, "07-editeur-schema", true);
  await page.getByRole("navigation", { name: "Sections du workflow" }).getByRole("link", { name: "Informations", exact: true }).click();
  await shot(page, "16-workflow-resume", true);
  await page.getByRole("navigation", { name: "Sections du workflow" }).getByRole("link", { name: "Entrées/Sorties", exact: true }).click();
  await shot(page, "17-workflow-entrees-sorties", true);
  await page.getByRole("navigation", { name: "Sections du workflow" }).getByRole("link", { name: "Conception", exact: true }).click();
  await menuClick(page, "Affichage", "Vue liste");
  await shot(page, "08-editeur-liste", true);
  await page.getByRole("button", { name: /Reboot_script/ }).first().click();
  await page.getByRole("tab", { name: "Script", exact: true }).click();
  await page.getByRole("button", { name: "Ouvrir l'éditeur de script" }).click();
  await expect(page.getByRole("dialog", { name: /Éditeur de script/ })).toBeVisible();
  await shot(page, "15-editeur-script");
  await page.getByRole("button", { name: "Fermer l'éditeur" }).click();

  await nav(page, "Workflows");
  await page.getByRole("link", { name: "Analyse avec validation", exact: true }).click();
  await page.getByRole("link", { name: /^Lancer/ }).first().click();
  await page.getByLabel(/Texte à analyser/).fill("un deux trois quatre cinq");
  await page.getByRole("button", { name: "Lancer l'automatisation" }).click();
  await expect(page.getByText("Une approbation est attendue")).toBeVisible({ timeout: 30_000 });
  await shot(page, "09-execution-en-attente-approbation", true);

  await page.goto("/page-inexistante");
  await expect(page.getByRole("heading", { name: "Page introuvable" })).toBeVisible();
  await shot(page, "18-page-introuvable");

  // Espace « Vitrine » (scripts/vitrine.sh) : captures facultatives, seulement si l'espace existe
  const vitrine = await page.evaluate(async () => (await fetch("/api/v1/workspaces/vitrine/workflows/")).status);
  if (vitrine === 200) {
    await page.getByLabel("Espace").selectOption("vitrine");
    await expect(page.getByLabel("Espace")).toHaveValue("vitrine");
    await page.evaluate(() => localStorage.setItem("orch.schema.view", "canvas")); // la vue liste a été choisie plus haut
    await page.waitForTimeout(500); // le choix d'espace est mémorisé par un effet : laissez-lui le temps avant de recharger
    await page.goto("/workflows/gestion-incident/schema");
    await expect(page.locator(".flow-node").first()).toBeVisible();
    await shot(page, "25-vitrine-schema");
    await page.goto("/workflows/traiter-lot-vms/schema");
    await expect(page.locator(".flow-node").first()).toBeVisible();
    await shot(page, "26-vitrine-lot-imbrique");
    const runId = await page.evaluate(async () => (await (await fetch("/api/v1/workspaces/vitrine/workflow-runs/?workflow=gestion-incident")).json()).results?.[0]?.id);
    if (runId) {
      await page.goto(`/workflows/gestion-incident/follow?run=${runId}`);
      await expect(page.locator(".flow-node.state-succeeded").first()).toBeVisible();
      await shot(page, "27-vitrine-suivi", true);
    }
    await page.getByLabel("Espace").selectOption("demo");
  }

  const mobile = await (await browser.newContext({ viewport: { width: 390, height: 800 } })).newPage();
  await login(mobile, "claire");
  await nav(mobile, "Approbations");
  await expect(mobile.getByRole("heading", { name: "Approbations" })).toBeVisible();
  await shot(mobile, "10-approbation-mobile");
  await mobile.getByRole("button", { name: /Examiner/ }).first().click();
  await shot(mobile, "20-approbation-examen");

  await nav(page, "Mes exécutions");
  await shot(page, "11-mes-executions");
  await nav(page, "Connexions");
  await shot(page, "12-connexions");
  await page.getByRole("button", { name: "Nouvelle connexion" }).click();
  await shot(page, "19-connexion-assistant");
  await page.keyboard.press("Escape");
  await nav(page, "Bibliothèques");
  await shot(page, "13-bibliotheques");
  await page.getByRole("list", { name: "Bibliothèques" }).getByRole("link", { name: "Skills IA", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Skills IA" })).toBeVisible();
  await shot(page, "23-skills-ia");
  await page.getByRole("link", { name: "Qualifier un incident", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Qualifier un incident" })).toBeVisible();
  await shot(page, "24-skill-detail");
  await nav(page, "Déclencheurs");
  await shot(page, "14-declencheurs");
  await page.getByRole("button", { name: "Nouvelle planification" }).click();
  await shot(page, "21-planification");
  await page.keyboard.press("Escape");

  const admin = await (await browser.newContext()).newPage();
  await login(admin, "admin");
  await nav(admin, "Plate-forme");
  await expect(admin.getByRole("heading", { level: 1, name: "Plate-forme" })).toBeVisible();
  await shot(admin, "22-plateforme");
  await admin.getByRole("tab", { name: "Utilisateurs" }).click();
  await expect(admin.getByRole("cell", { name: /alice/ }).first()).toBeVisible();
  await shot(admin, "29-utilisateurs");
  await admin.getByRole("tab", { name: "Runners" }).click();
  await shot(admin, "30-runners");
  await nav(admin, "Administration");
  await expect(admin.getByText("L'orchestrateur est actif.")).toBeVisible({ timeout: 15_000 });
  await shot(admin, "15-administration-sante", true);
  await admin.getByLabel("Environnement").selectOption("prod");
  await shot(admin, "16-environnement-production");

  // Assistant IA de l'éditeur (faux fournisseur déterministe : la capture montre le fonctionnement, pas un vrai modèle)
  await admin.getByRole("tab", { name: "Assistant IA" }).click();
  try {
    await admin.getByLabel("Mode").selectOption({ label: "Local strict (aucun envoi cloud, aucun repli externe)" });
    await admin.getByLabel(/Budget quotidien/).fill("50000000"); // le faux fournisseur compte des jetons : les essais répétés épuisent le budget du jour
    await admin.getByRole("button", { name: "Enregistrer", exact: true }).click();
    await expect(admin.getByText("Enregistré.")).toBeVisible();
    if ((await admin.getByText(/^fake — /).count()) === 0) {
      await admin.getByLabel("Nom", { exact: true }).fill("fake");
      await admin.getByLabel("Type").selectOption("fake");
      await admin.getByRole("button", { name: "Ajouter" }).click();
      await expect(admin.getByText(/^fake — /)).toBeVisible();
    }
    await page.goto("/workflows/analyse-validee/schema");
    await page.evaluate(() => localStorage.setItem("orch.schema.view", "canvas"));
    await page.reload();
    await page.getByRole("button", { name: "Assistant IA" }).click();
    const chat = page.getByRole("region", { name: "Assistant IA" });
    await chat.getByLabel("Votre demande à l'assistant").fill("Ajoute un script à la fin du workflow");
    await chat.getByRole("button", { name: "Envoyer" }).click();
    await expect(chat.getByText("Modifications proposées")).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "Palette", exact: true }).click(); // plus de place pour le schéma sur la capture
    await shot(page, "31-assistant-ia-editeur");
  } finally {
    await admin.getByLabel("Mode").selectOption({ label: "IA désactivée" });
    await admin.getByRole("button", { name: "Enregistrer", exact: true }).click();
  }
});
