import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, goNav, login } from "./helpers";

const nav = goNav;
const tab = (page: Page, name: string) => page.getByRole("tab", { name }).click();
const uniq = () => `${Date.now()}`.slice(-7);

test("administrateur : santé du système compréhensible, membres, accessibilité", async ({ page }) => {
  await login(page, "admin");
  await nav(page, "Administration");
  await expect(page.getByRole("heading", { name: "État du système" })).toBeVisible();
  await expect(page.getByText("L'orchestrateur est actif.")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("worker(s) actif(s).")).toBeVisible();
  await expectNoA11yViolations(page);
  await tab(page, "Membres");
  await expect(page.getByRole("heading", { name: "Membres de l'espace" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "alice", exact: true })).toBeVisible();
  await expectNoA11yViolations(page);
  await tab(page, "Politiques");
  await expect(page.getByLabel(/promue avant de s'exécuter en production/)).toBeChecked();
  await tab(page, "Audit");
  await expect(page.getByRole("heading", { name: "Journal d'audit" })).toBeVisible();
  await expectNoA11yViolations(page);
});

test("administration refusée à un concepteur (menu absent, accès direct bloqué)", async ({ page }) => {
  await login(page, "alice");
  await expect(page.getByRole("navigation", { name: "Navigation principale" }).getByRole("link", { name: "Administration" })).toHaveCount(0);
  await page.goto("/admin");
  await expect(page.getByText("L'administration est réservée")).toBeVisible();
});

test("administrateur : compte de service et jeton affiché une seule fois, puis révoqué", async ({ page }) => {
  const id = uniq();
  await login(page, "admin");
  await nav(page, "Administration");
  await tab(page, "Jetons et comptes de service");
  await page.getByRole("button", { name: "Nouveau compte de service" }).click();
  const saDlg = page.getByRole("dialog", { name: "Nouveau compte de service" });
  await saDlg.getByLabel("Nouveau compte de service").fill(`bot${id}`);
  await saDlg.getByRole("button", { name: "Créer", exact: true }).click();
  await expect(saDlg).toHaveCount(0);
  await page.getByRole("button", { name: "Nouveau jeton", exact: true }).click();
  const tokDlg = page.getByRole("dialog", { name: "Nouveau jeton d'API" });
  await tokDlg.getByLabel("Compte de service", { exact: true }).selectOption({ label: `bot${id}` });
  await tokDlg.getByLabel("Nom du jeton").fill(`ci-${id}`);
  await tokDlg.getByRole("button", { name: "Créer le jeton" }).click();
  const box = page.locator(".onetime");
  await expect(box).toContainText("ne sera plus jamais affiché");
  const token = (await box.locator("code").innerText()).trim();
  expect(token.startsWith("orch_")).toBe(true);
  await expectNoA11yViolations(page);
  await tokDlg.getByRole("button", { name: "J'ai copié le jeton" }).click();
  await expect(page.getByText(token)).toHaveCount(0); // fermé : le jeton disparaît de l'écran
  await page.reload();
  await nav(page, "Administration");
  await tab(page, "Jetons et comptes de service");
  await expect(page.getByText(token)).toHaveCount(0); // never shown again, only the short prefix
  await page.getByRole("button", { name: `Révoquer le jeton ci-${id}` }).click();
  await page.getByRole("dialog", { name: "Révoquer ce jeton ?" }).getByRole("button", { name: "Révoquer le jeton", exact: true }).click();
  await page.getByLabel("Afficher").selectOption("all");
  await expect(page.getByRole("row", { name: new RegExp(`ci-${id}.*Révoqué`) })).toBeVisible();
});

test("connexion : assistant en 4 étapes, test sans révéler le secret", async ({ page }) => {
  const id = uniq();
  const secret = `s3cret-${id}-valeur-longue`;
  await login(page, "alice");
  await nav(page, "Connexions");
  await page.getByRole("button", { name: "Nouvelle connexion" }).click();
  const dlg = page.getByRole("dialog", { name: "Nouvelle connexion" });
  await expect(dlg.getByText(/étape 1 sur 4/)).toBeVisible();
  await page.getByRole("button", { name: "Continuer" }).click();
  await page.getByLabel("Nom lisible").fill(`Service ${id}`);
  await page.getByLabel("Identifiant technique").fill(`svc-${id}`);
  await page.getByRole("button", { name: "Continuer" }).click();
  await page.getByLabel(/Secret \(jeton/).fill(secret);
  await page.getByRole("button", { name: "Enregistrer la connexion" }).click();
  await expect(page.getByText(/Connexion « Service .* » enregistrée/)).toBeVisible();
  await page.getByRole("button", { name: "Tester maintenant" }).click();
  await expect(page.getByText("Le secret est présent et déchiffrable.").first()).toBeVisible();
  expect(await page.content()).not.toContain(secret);
  await expectNoA11yViolations(page);
});

test("promotion en production : demande, revue par une autre personne, puis lancement", async ({ browser }) => {
  const designer = await (await browser.newContext()).newPage();
  await login(designer, "alice");
  // a brand-new workflow each time, so the whole request -> review -> run flow is always exercised
  const slug = `promo-${uniq()}`;
  const status = await designer.evaluate(async (slug) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    const h = { "Content-Type": "application/json", "X-CSRFToken": csrf };
    const base = "/api/v1/workspaces/demo";
    await fetch(`${base}/workflows/`, { method: "POST", headers: h, body: JSON.stringify({ slug, name: `Promo ${slug}` }) });
    const graph = { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [],
      nodes: [{ id: "a", type: "action", name: "Analyser", action: "text-stats", inputs: { text: "promotion" } }] };
    await fetch(`${base}/workflows/${slug}/draft/`, { method: "PUT", headers: h, body: JSON.stringify({ graph, revision: 1 }) });
    return (await fetch(`${base}/workflows/${slug}/publish/`, { method: "POST", headers: h, body: "{}" })).status;
  }, slug);
  expect(status).toBe(201);
  await designer.getByLabel("Environnement").selectOption("prod");
  await designer.goto(`/workflows/${slug}/run`);
  await expect(designer.getByText(/n'est pas encore promue en production/)).toBeVisible();
  await designer.getByRole("button", { name: "Demander la promotion en production" }).click();
  await expect(designer.getByText(/en attente de revue par une autre personne/)).toBeVisible();

  const reviewer = await (await browser.newContext()).newPage();
  await login(reviewer, "admin");
  await nav(reviewer, "Approbations");
  const examine = reviewer.getByRole("button", { name: new RegExp(`Examiner la promotion de Promo ${slug}`) });
  await examine.click();
  await reviewer.getByRole("dialog").getByRole("button", { name: "Approuver la promotion" }).click();
  await expect(examine).toHaveCount(0, { timeout: 15_000 });

  await designer.reload();
  await expect(designer.getByText(/est promue en production/)).toBeVisible();
  await designer.getByRole("button", { name: "Lancer l'automatisation" }).click();
  await expect(designer.getByText(/Environnement : Production/)).toBeVisible();
  await expect(designer.getByText("L'exécution s'est terminée avec succès.")).toBeVisible({ timeout: 60_000 });
});

test("assistant IA : désactivé par défaut puis brouillon généré (faux fournisseur local strict)", async ({ browser }) => {
  const id = uniq();
  const admin = await (await browser.newContext()).newPage();
  await login(admin, "admin");
  await nav(admin, "Administration");
  await tab(admin, "Assistant IA");
  await admin.getByLabel("Mode").selectOption({ label: "Local strict (aucun envoi cloud, aucun repli externe)" });
  await admin.getByLabel(/Budget quotidien/).fill("50000000"); // le faux fournisseur compte des jetons : les essais répétés épuisent le budget du jour
  await admin.getByRole("button", { name: "Enregistrer", exact: true }).click();
  await expect(admin.getByText("Enregistré.")).toBeVisible();
  if ((await admin.getByText(/^fake — /).count()) === 0) {
    await admin.getByRole("button", { name: "Ajouter un fournisseur" }).click();
    const prov = admin.getByRole("dialog", { name: "Ajouter un fournisseur" });
    await prov.getByLabel("Nom", { exact: true }).fill("fake");
    await prov.getByLabel("Type").selectOption("fake");
    await prov.getByRole("button", { name: "Ajouter", exact: true }).click();
    await expect(admin.getByText(/^fake — /)).toBeVisible();
  }
  await expectNoA11yViolations(admin);

  const designer = await (await browser.newContext()).newPage();
  await login(designer, "alice");
  await nav(designer, "Workflows");
  await designer.getByRole("button", { name: "Générer avec l'IA" }).click();
  const aiDlg = designer.getByRole("dialog", { name: "Générer un brouillon avec l'assistant IA" });
  await aiDlg.getByLabel("Nom du brouillon").fill(`Brouillon IA ${id}`);
  await aiDlg.getByLabel("Décrivez en français ce que le workflow doit faire").fill("Analyser des statistiques de texte");
  await expectNoA11yViolations(designer);
  await aiDlg.getByRole("button", { name: "Proposer un plan" }).click();
  const planDlg = designer.getByRole("dialog", { name: /Plan envisagé/ });
  await expect(planDlg.getByLabel(/Ce que fera le workflow/)).toBeVisible({ timeout: 30_000 });
  await expectNoA11yViolations(designer);
  await planDlg.getByRole("button", { name: "Valider et générer" }).click();
  const report = designer.getByRole("dialog", { name: "Brouillon généré" });
  await expect(report).toContainText(/étape\(s\) proposée\(s\)/, { timeout: 20_000 });
  await expect(report).toContainText("Aucun problème détecté");
  await expectNoA11yViolations(designer);
  await report.getByRole("button", { name: "Ouvrir dans l'éditeur" }).click();
  await expect(designer.getByText(/Ce brouillon a été généré par IA/)).toBeVisible({ timeout: 20_000 });
  await expect(designer.locator(".flow-node").first()).toBeVisible(); // le brouillon généré d'après le plan validé est ouvert
  await designer.getByRole("navigation", { name: "Sections du workflow" }).getByRole("link", { name: "Historique des versions" }).click();
  await expect(designer.getByRole("button", { name: "Publier une nouvelle version" })).toBeVisible(); // jamais publié automatiquement

  // restore the default (AI off) so other tests/demos start from a clean, safe state
  await admin.getByLabel("Mode").selectOption({ label: "IA désactivée" });
  await admin.getByRole("button", { name: "Enregistrer", exact: true }).click();
  await expect(admin.getByText("Enregistré.")).toBeVisible();
});

test("paquet : export puis aperçu d'import (éléments identiques) sans rien modifier", async ({ page }) => {
  const id = uniq();
  await login(page, "alice");
  await nav(page, "Bibliothèques");
  await tab(page, "Paquets");
  await page.getByRole("button", { name: "Exporter un paquet" }).click();
  const exportDlg = page.getByRole("dialog", { name: "Exporter un paquet" });
  await exportDlg.getByLabel("Nom", { exact: true }).fill(`Paquet ${id}`);
  await exportDlg.getByLabel("Identifiant", { exact: true }).fill(`pkg-${id}`);
  await exportDlg.getByRole("group", { name: "Actions à inclure" }).getByLabel(/Statistiques de texte/).check();
  await exportDlg.getByRole("button", { name: "Créer le paquet" }).click();
  await expect(page.getByText("Paquet créé")).toBeVisible();
  await page.getByLabel("Rechercher un paquet").fill(`Paquet ${id}`); // la liste est paginée
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("row", { name: new RegExp(`Paquet ${id}`) }).getByRole("link", { name: ".zip" }).click()]);
  const path = await download.path();
  await page.getByRole("button", { name: "Importer un paquet" }).click();
  await page.getByLabel(/Archive de paquet/).setInputFiles(path);
  await page.getByRole("button", { name: /Aperçu/ }).click();
  await expect(page.getByRole("region", { name: "Rapport d'import" })).toContainText("Identique");
  await expectNoA11yViolations(page);
});

test("déclencheurs et bibliothèque : écrans accessibles", async ({ page }) => {
  await login(page, "alice");
  await nav(page, "Déclencheurs");
  await expect(page.getByRole("tab", { name: "Planifications" })).toBeVisible();
  await expectNoA11yViolations(page);
  await page.getByRole("button", { name: "Nouvelle planification" }).click();
  await expect(page.getByRole("dialog", { name: "Nouvelle planification" })).toBeVisible();
  await expectNoA11yViolations(page);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Nouvelle planification" })).toBeFocused(); // le focus revient à l'ouvreur
  await page.getByRole("tab", { name: "Webhooks entrants" }).click();
  await expectNoA11yViolations(page);
  await nav(page, "Bibliothèques");
  await expect(page.getByRole("tab", { name: "Actions", selected: true })).toBeVisible();
  await page.getByRole("button", { name: "Nouvelle action" }).click();
  await expect(page.getByRole("dialog", { name: "Nouvelle action" })).toBeVisible();
  await expectNoA11yViolations(page);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByLabel("Rechercher une action").fill("Statistiques de texte");
  await page.getByRole("link", { name: "Statistiques de texte" }).click();
  await expect(page.getByRole("tab", { name: "Contrat", selected: true })).toBeVisible();
  await page.getByRole("tab", { name: /^Versions/ }).click();
  await expect(page.getByRole("table", { name: "Versions de l'action" })).toBeVisible();
  await expectNoA11yViolations(page);
});

test("administrateur de plate-forme : créer, renommer, archiver et restaurer un espace ; voir les processus ; journal", async ({ page }) => {
  const id = uniq();
  await login(page, "admin");
  await nav(page, "Plate-forme");
  await expect(page.getByRole("heading", { level: 1, name: "Plate-forme" })).toBeVisible();
  await expectNoA11yViolations(page);

  await page.getByRole("button", { name: "Nouvel espace" }).click();
  const dlg = page.getByRole("dialog", { name: "Nouvel espace" });
  await dlg.getByLabel("Nom de l'espace").fill(`Espace ${id}`);
  await dlg.getByLabel(/Identifiant \(lettres/).fill(`sp-${id}`);
  await expectNoA11yViolations(page);
  await dlg.getByRole("button", { name: "Créer l'espace" }).click();
  await expect(dlg).toHaveCount(0);
  await page.getByLabel("Rechercher un espace").fill(`sp-${id}`); // la liste est paginée : on retrouve l'espace par son identifiant
  const row = page.getByRole("row", { name: new RegExp(`sp-${id}`) });
  await expect(row).toContainText("✔ Actif");
  await expect(row).toContainText("admin"); // sans administrateur indiqué, le demandeur l'est

  await row.getByRole("button", { name: new RegExp(`Renommer l'espace Espace ${id}`) }).click();
  await page.getByRole("dialog", { name: "Renommer l'espace" }).getByLabel("Nom de l'espace").fill(`Renommé ${id}`);
  await page.getByRole("dialog").getByRole("button", { name: "Renommer", exact: true }).click();
  await expect(page.getByRole("row", { name: new RegExp(`Renommé ${id}`) })).toBeVisible();

  const target = page.getByRole("row", { name: new RegExp(`sp-${id}`) });
  const confirmIn = (title: string, button: string) => page.getByRole("dialog", { name: title }).getByRole("button", { name: button, exact: true }).click();
  await target.getByRole("button", { name: /Archiver l'espace/ }).click();
  await confirmIn("Archiver cet espace ?", "Confirmer l'archivage");
  await expect(target).toContainText("⏸ Archivé");
  await target.getByRole("button", { name: /Restaurer l'espace/ }).click();
  await confirmIn("Restaurer cet espace ?", "Confirmer la restauration");
  await expect(target).toContainText("✔ Actif");
  await target.getByRole("button", { name: /Archiver l'espace/ }).click(); // état final propre : l'espace de test est archivé
  await confirmIn("Archiver cet espace ?", "Confirmer l'archivage");
  await expect(target).toContainText("⏸ Archivé");

  await tab(page, "Processus");
  await expect(page.getByRole("row", { name: /Orchestrateur/ }).getByText("✔ actif")).toBeVisible({ timeout: 15_000 });
  await expectNoA11yViolations(page);
  await tab(page, "Administrateurs");
  await expect(page.getByRole("cell", { name: /admin — vous/ })).toBeVisible();
  await expectNoA11yViolations(page);
  await tab(page, "Journal");
  await expect(page.getByRole("row", { name: new RegExp(`Espace archivé.*sp-${id}`) }).first()).toBeVisible();
  await expect(page.getByRole("row", { name: new RegExp(`Espace créé.*sp-${id}`) })).toBeVisible();
  await expectNoA11yViolations(page);
});

test("plate-forme refusée à un administrateur d'espace non plate-forme : ni menu ni accès direct, et pas de processus dans la Santé", async ({ page }) => {
  await login(page, "alice");
  await expect(page.getByRole("navigation", { name: "Navigation principale" }).getByRole("link", { name: "Plate-forme" })).toHaveCount(0);
  await page.goto("/platform");
  await expect(page.getByRole("heading", { level: 1, name: "Page introuvable" })).toBeVisible();
  const status = await page.evaluate(async () => (await fetch("/api/v1/platform/workers/", { credentials: "same-origin" })).status);
  expect(status).toBe(403); // le serveur refuse, l'interface ne fait que masquer
});
