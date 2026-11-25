import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, goNav, login, menuClick, publishViaApi, boxOf } from "./helpers";

const uniq = () => `${Date.now()}`.slice(-7);
const tabLink = (page: Page, name: string) => page.getByRole("navigation", { name: "Sections du workflow" }).getByRole("link", { name, exact: true }).click();
const addTile = async (page: Page, label: string) => { await page.getByLabel("Rechercher un élément").fill(label); await page.getByRole("button", { name: `Ajouter : ${label}`, exact: true }).click(); await page.getByLabel("Rechercher un élément").fill(""); };
const panelTab = (page: Page, name: string) => page.getByRole("tab", { name, exact: true }).click();

async function createWorkflow(page: Page, name: string) {
  await goNav(page, "Workflows");
  await page.getByRole("button", { name: "Nouveau workflow" }).click();
  await page.getByLabel("Nom du nouveau workflow").fill(name);
  await page.getByRole("button", { name: "Créer et ouvrir l'éditeur" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(name);
}

test("concepteur : workflow à 3 étapes avec condition, sans code, publié puis exécuté", async ({ page }) => {
  await login(page);
  await goNav(page, "Workflows");
  await expectNoA11yViolations(page);
  await createWorkflow(page, `Analyse E2E ${uniq()}`);

  // 1) entrée du workflow (formulaire de lancement)
  await tabLink(page, "Entrées/Sorties");
  await page.getByRole("button", { name: "Ajouter une entrée" }).click();
  await page.getByLabel("Identifiant technique de l'entrée 1").fill("texte");
  await page.getByLabel(/^Libellé de/).fill("Texte");
  await page.getByRole("checkbox", { name: /obligatoire/ }).check();
  await expectNoA11yViolations(page);

  // 2) action du catalogue, paramètre relié à l'entrée par le sélecteur de variables
  await tabLink(page, "Conception");
  await addTile(page, "Statistiques de texte");
  await panelTab(page, "Entrées");
  await page.getByLabel("Insérer une variable pour Texte").selectOption({ label: "Entrée « Texte »" });

  // 3) décision fondée sur la sortie de l'étape précédente
  await addTile(page, "Décision");
  await panelTab(page, "Décision");
  await page.getByLabel("Insérer une variable dans la condition").selectOption({ label: "Statistiques de texte → Mots" });
  await page.getByLabel("Condition", { exact: true }).press("End");
  await page.getByLabel("Condition", { exact: true }).pressSequentially(" > 3");

  // 4) étape suivie si la condition est vraie
  await addTile(page, "Attente simulée");
  await panelTab(page, "Entrées");
  await page.getByLabel(/^Secondes/).fill("1");

  await expect(page.getByRole("button", { name: /✔ Valide/ })).toBeVisible();
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 30_000 }); // l'enregistrement automatique est différé puis validé côté serveur
  await expect(page.locator(".flow-node")).toHaveCount(3);
  await expectNoA11yViolations(page);
  await menuClick(page, "Affichage", "Vue liste"); // même contenu, sans canevas
  await expect(page.getByRole("list", { name: "Étapes du workflow" }).locator("li")).toHaveCount(3);

  await tabLink(page, "Versions");
  const [pub] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/publish/") && r.request().method() === "POST", { timeout: 30_000 }),
    page.getByRole("button", { name: "Publier une nouvelle version" }).click(),
  ]);
  expect(pub.status(), await pub.text()).toBe(201); // si la publication échoue, on lit pourquoi
  await expect(page.getByText(/Version 1\.0\.0 publiée/)).toBeVisible();
  await page.getByRole("link", { name: /Lancer v1\.0\.0/ }).click();
  await page.getByLabel(/Texte/).fill("un deux trois quatre cinq");
  await page.getByRole("button", { name: "Lancer l'automatisation" }).click();

  await expect(page.getByText("L'exécution s'est terminée avec succès.")).toBeVisible({ timeout: 100_000 });
  await expect(page.getByText(/3 étape\(s\) réussie\(s\)/)).toBeVisible();
  await expect(page.locator(".flow-node.state-succeeded")).toHaveCount(3); // le schéma d'exécution montre l'état de chaque étape
  await expectNoA11yViolations(page);
});

test("concepteur : tâche script — éditeur de code, test isolé, publication et exécution", async ({ page }) => {
  await login(page);
  await createWorkflow(page, `Script E2E ${uniq()}`);
  await tabLink(page, "Entrées/Sorties");
  await page.getByRole("button", { name: "Ajouter une entrée" }).click();
  await page.getByLabel("Identifiant technique de l'entrée 1").fill("noms");
  await page.getByLabel(/^Libellé de/).fill("Noms");
  await page.getByRole("checkbox", { name: /obligatoire/ }).check();

  await tabLink(page, "Conception");
  await addTile(page, "Tâche script");
  await panelTab(page, "Entrées/Sorties");
  await page.getByLabel("Nom de la variable 1").count(); // la tâche n'a pas encore d'entrée liée
  await page.getByRole("button", { name: "Ajouter une entrée" }).click();
  await page.getByLabel("Nom de la variable 1").fill("liste");
  await page.getByLabel(/Valeur de la variable/).fill("{{ inputs.noms }}");
  await page.getByLabel("Nom de la sortie 1").fill("total");
  await page.getByLabel(/Type de la sortie/).selectOption("integer");

  // l'éditeur de code, accessible depuis l'étape
  await panelTab(page, "Script");
  await page.getByRole("button", { name: "Ouvrir l'éditeur de script" }).click();
  const dialog = page.getByRole("dialog", { name: /Éditeur de script/ });
  await expect(dialog).toBeVisible();
  await expectNoA11yViolations(page);
  const editor = dialog.getByRole("textbox", { name: /Code du script/ });
  await editor.click();
  await page.keyboard.press("Control+A");
  await page.keyboard.type("noms = [n.strip() for n in liste.split(',') if n.strip()]\nSystem.log('noms reçus :', noms)\ntotal = len(noms)\n");

  // test en isolation (conteneur), avec des valeurs d'essai
  await dialog.getByRole("tab", { name: "Test", exact: true }).click();
  await dialog.getByLabel("Valeurs des entrées liées (JSON)").fill('{"liste": "Ada, Grace, Linus"}');
  await dialog.getByRole("button", { name: /Tester le script/ }).click();
  await expect(dialog.getByRole("tab", { name: "Résultat", selected: true })).toBeVisible({ timeout: 90_000 });
  await expect(dialog.getByText("Le script s'est terminé avec succès.")).toBeVisible();
  await expect(dialog.locator("pre").last()).toContainText('"total": 3');
  await dialog.getByRole("tab", { name: "Journal" }).click();
  await expect(dialog.getByRole("log", { name: "Journal du test" })).toContainText("noms reçus");
  await dialog.getByRole("button", { name: "Fermer l'éditeur" }).click();
  await expect(dialog).toHaveCount(0);

  await tabLink(page, "Entrées/Sorties");
  await page.getByRole("button", { name: "Ajouter une sortie" }).click();
  await page.getByLabel("Nom de la sortie 1").fill("nombre");
  await page.getByLabel("Valeur de la sortie nombre").fill("{{ steps.script.output.total }}");
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 30_000 }); // l'enregistrement automatique est différé puis validé côté serveur
  await tabLink(page, "Versions");
  await page.getByRole("button", { name: "Publier une nouvelle version" }).click();
  await expect(page.getByText(/Version 1\.0\.0 publiée/)).toBeVisible();
  await page.getByRole("link", { name: /Lancer v1\.0\.0/ }).click();
  await page.getByLabel(/noms/i).fill("a, b, c, d");
  await page.getByRole("button", { name: "Lancer l'automatisation" }).click();
  await expect(page.getByText("L'exécution s'est terminée avec succès.")).toBeVisible({ timeout: 90_000 });
  await page.getByRole("link", { name: /Détail complet/ }).click(); // le suivi se fait sur le schéma ; résultat et journaux sur la page de détail
  await expect(page.getByRole("heading", { name: "Résultat" })).toBeVisible();
  await page.getByRole("tab", { name: "Journaux" }).click();
  await expect(page.getByLabel(/Journal de Tâche script/)).toContainText("noms reçus");
});

test("clavier seul : ajouter une étape, ouvrir l'éditeur, écrire du code et en sortir sans souris", async ({ page }) => {
  await login(page);
  const slug = `kbd-${uniq()}`;
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [], nodes: [{ id: "a", type: "wait", name: "Pause", seconds: 1 }] });
  await page.goto(`/workflows/${slug}/schema`);
  await menuClick(page, "Affichage", "Vue liste");
  await expect(page.getByRole("list", { name: "Étapes du workflow" })).toBeVisible();
  await page.getByRole("button", { name: "Ajouter : Tâche script", exact: true }).focus();
  await page.keyboard.press("Enter"); // sélectionne la nouvelle étape et ouvre ses propriétés
  await page.getByRole("tab", { name: "Script" }).focus();
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Ouvrir l'éditeur de script" }).focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: /Éditeur de script/ });
  await expect(dialog).toBeFocused();
  await dialog.getByRole("textbox", { name: /Code du script/ }).focus();
  await page.keyboard.press("Control+A");
  await page.keyboard.type("resultat = 'clavier'");
  // Échap ferme d'abord la liste de suggestions si elle est ouverte, puis quitte l'éditeur (sans fermer la boîte de dialogue)
  for (let i = 0; i < 2 && (await dialog.getByRole("textbox", { name: /Code du script/ }).evaluate((el) => el === document.activeElement)); i++) await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("textbox", { name: /Code du script/ })).not.toBeFocused();
  await page.keyboard.press("Escape"); // hors de l'éditeur : ferme la boîte, le focus revient à l'ouvreur
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Ouvrir l'éditeur de script" })).toBeFocused();
});

test("favoris et dossiers : étoile, accueil, arbre de dossiers", async ({ page }) => {
  await login(page);
  await goNav(page, "Workflows");
  await page.getByRole("button", { name: /Démo/ }).first().click();
  await expect(page.getByRole("link", { name: "Analyse avec validation", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Redémarrer des VM", exact: true })).toHaveCount(0); // rangé dans un sous-dossier : « Démo » n'affiche que ses propres éléments
  await page.getByRole("button", { name: /Infrastructure/ }).click();
  await expect(page.getByRole("link", { name: "Redémarrer des VM", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Analyse avec validation", exact: true })).toHaveCount(0);
  const row = page.getByRole("row", { name: /Redémarrer des VM/ });
  await expect(row.getByRole("button", { name: /favoris/ })).toBeVisible();
  await page.waitForLoadState("networkidle");
  await row.getByRole("button", { name: /^Ajouter .* aux favoris/ }).click();
  await expect(page.getByRole("button", { name: "Retirer Redémarrer des VM des favoris" })).toHaveAttribute("aria-pressed", "true");
  await goNav(page, "Accueil");
  await expect(page.getByRole("heading", { name: "Favoris" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Favoris" }).getByRole("link", { name: "Redémarrer des VM", exact: true })).toBeVisible();
  await goNav(page, "Workflows");
  await page.getByLabel("Rechercher un workflow").fill("Redémarrer des VM"); // la liste est paginée
  await page.getByRole("row", { name: /Redémarrer des VM/ }).getByRole("button", { name: /favoris/ }).click(); // état initial rétabli
  await expect(page.getByRole("row", { name: /Redémarrer des VM/ }).getByRole("button", { name: /Ajouter .* aux favoris/ })).toBeVisible();
});

test("assistant de lancement par étapes, schéma d'exécution coloré et journaux (exemple « Redémarrer des VM »)", async ({ page }) => {
  await login(page);
  await goNav(page, "Workflows");
  await page.getByLabel("Rechercher un workflow").fill("Redémarrer des VM"); // la liste est paginée
  await page.getByRole("row", { name: /Redémarrer des VM/ }).getByRole("link", { name: /Lancer/ }).click();
  await expect(page.getByRole("heading", { name: /Redémarrer des VM/ })).toBeVisible();
  await page.getByRole("button", { name: "Lancer l'automatisation" }).click(); // champ obligatoire vide
  await expect(page.getByLabel(/Adresses IP des VM/)).toHaveAttribute("aria-invalid", "true");
  await page.getByLabel(/Adresses IP des VM/).fill("10.0.0.1, 10.0.0.2");
  await page.getByRole("button", { name: "Lancer l'automatisation" }).click();
  await expect(page.getByText("L'exécution s'est terminée avec succès.")).toBeVisible({ timeout: 90_000 });
  await expect(page.locator(".flow-node.state-succeeded")).toHaveCount(1);
  await page.locator(".flow-node").click();
  await expect(page.getByLabel(/Journal de Reboot_script/)).toContainText("redémarrage simulé");
  await expect(page.getByText(/1\/1 étape\(s\) terminée\(s\)/)).toBeVisible();
  await page.getByRole("link", { name: /Détail complet/ }).click();
  await page.getByRole("tab", { name: "Journaux" }).click();
  await expect(page.getByLabel(/Journal de Reboot_script/)).toContainText("10.0.0.2");
  await page.getByRole("tab", { name: "Entrées/Sorties" }).click();
  await expect(page.getByText("2 machine(s) redémarrée(s) (simulation)").first()).toBeVisible();
  await expectNoA11yViolations(page);
});

test("formulaire d'entrée par étapes : l'assistant guide, bloque et récapitule", async ({ page }) => {
  await login(page);
  const slug = `wiz-${uniq()}`;
  const status = await publishViaApi(page, slug, {
    inputs: { type: "object", required: ["nom", "ville"], properties: { nom: { type: "string", title: "Nom" }, ville: { type: "string", title: "Ville" } } },
    form: { steps: [{ title: "Identité", fields: ["nom"] }, { title: "Lieu", description: "Où habitez-vous ?", fields: ["ville"] }] },
    variables: [], notes: [], outputs: { v: "{{ inputs.ville }}" }, edges: [],
    nodes: [{ id: "n", type: "action", name: "Analyser", action: "text-stats", inputs: { text: "{{ inputs.nom }}" } }],
  });
  expect(status).toBe(201);
  await page.goto(`/workflows/${slug}/run`);
  await expect(page.getByRole("navigation", { name: "Étapes du formulaire" })).toBeVisible();
  await page.getByRole("button", { name: "Suivant" }).click();
  await expect(page.getByRole("alert")).toContainText("obligatoire");
  await page.getByLabel(/Nom/).fill("Ada");
  await page.getByRole("button", { name: "Suivant" }).click();
  await expect(page.getByRole("heading", { name: /2\. Lieu/ })).toBeVisible();
  await expect(page.getByText("Où habitez-vous ?")).toBeVisible();
  await expectNoA11yViolations(page);
  await page.getByRole("button", { name: "Précédent" }).click();
  await expect(page.getByLabel(/Nom/)).toHaveValue("Ada");
  await page.getByRole("button", { name: "Suivant" }).click();
  await page.getByLabel(/Ville/).fill("Lyon");
  await page.getByRole("button", { name: "Lancer l'automatisation" }).click();
  await expect(page.getByText("L'exécution s'est terminée avec succès.")).toBeVisible({ timeout: 100_000 });
});

test("tous les onglets d'un workflow sont accessibles (axe) et cohérents", async ({ page }) => {
  await login(page);
  await page.goto("/workflows/redemarrer-vms");
  for (const name of ["Informations", "Variables", "Entrées/Sorties", "Conception", "Suivi", "Formulaire d'entrée", "Versions", "Activité", "Exécutions"]) {
    await tabLink(page, name);
    await expect(page.getByRole("navigation", { name: "Sections du workflow" }).getByRole("link", { name, exact: true })).toHaveAttribute("aria-current", "page");
    await expectNoA11yViolations(page);
  }
  await menuClick(page, "Plus d'actions", "Rechercher les dépendances");
  const deps = page.getByRole("dialog", { name: "Dépendances" });
  await expect(deps).toBeVisible();
  await expectNoA11yViolations(page);
  await deps.getByRole("button", { name: "Fermer", exact: true }).first().click(); // le volet est modal : on le ferme avant de continuer
  await expect(deps).toHaveCount(0);
  await page.getByRole("button", { name: "Valider", exact: true }).click();
  await expect(page.getByText(/Le workflow est valide/)).toBeVisible({ timeout: 15_000 });
});

test("approbateur sur mobile : approuve une demande depuis une vue compréhensible", async ({ browser }) => {
  const marker = `E2E-${Date.now()}`;
  const requester = await (await browser.newContext()).newPage();
  await login(requester, "alice");
  await goNav(requester, "Workflows");
  await requester.getByLabel("Rechercher un workflow").fill("Analyse avec validation"); // la liste est paginée
  await requester.getByRole("row", { name: /Analyse avec validation/ }).getByRole("link", { name: /Lancer/ }).click();
  await requester.getByLabel(/Texte à analyser/).fill(`un deux trois quatre ${marker}`);
  await requester.getByRole("button", { name: "Lancer l'automatisation" }).click();
  await expect(requester.getByText("Une approbation est attendue").first()).toBeVisible({ timeout: 60_000 });

  const mobile = await (await browser.newContext({ viewport: { width: 390, height: 800 } })).newPage();
  await login(mobile, "claire");
  await goNav(mobile, "Approbations");
  const card = mobile.locator(".approval-row", { hasText: marker });
  await expect(card).toContainText("Valider l'analyse");
  await expectNoA11yViolations(mobile);
  await card.getByRole("button", { name: /Examiner/ }).click();
  const dlg = mobile.getByRole("dialog");
  await expect(dlg.getByRole("heading", { name: /Valider l'analyse/ })).toBeVisible();
  await expectNoA11yViolations(mobile);
  await dlg.getByLabel("Commentaire").fill("Ok pour moi");
  await dlg.getByRole("button", { name: "Approuver", exact: true }).click();
  await expect(mobile.locator(".approval-row", { hasText: marker })).toHaveCount(0);

  await requester.reload();
  await expect(requester.getByText("L'exécution s'est terminée avec succès.")).toBeVisible({ timeout: 30_000 });
});

test("un concepteur ne peut pas approuver ; un lecteur ne peut pas éditer", async ({ browser }) => {
  const viewer = await (await browser.newContext()).newPage();
  await login(viewer, "bob");
  await goNav(viewer, "Workflows");
  await expect(viewer.getByRole("button", { name: "Nouveau workflow" })).toHaveCount(0);
  await viewer.getByLabel("Rechercher un workflow").fill("Analyse avec validation"); // la liste est paginée
  await viewer.getByRole("row", { name: /Analyse avec validation/ }).getByRole("link", { name: "Consulter" }).click();
  await expect(viewer.getByText("accès en lecture seule")).toBeVisible();
  await expect(viewer.getByRole("button", { name: "Supprimer" })).toHaveCount(0);
  await expect(viewer.getByRole("button", { name: "Annuler" })).toBeDisabled();
  await tabLink(viewer, "Versions");
  await expect(viewer.getByRole("button", { name: "Publier une nouvelle version" })).toHaveCount(0);
});

test("plusieurs exécutions simultanées : suivi sur le schéma, par exécution et vue agrégée", async ({ page }) => {
  await login(page);
  const slug = `multi-${uniq()}`;
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [], nodes: [{ id: "a", type: "wait", name: "Pause", seconds: 45 }] });
  const launch = () => page.evaluate(async (s) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    const r = await fetch(`/api/v1/workspaces/demo/workflows/${s}/runs/`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify({ environment: "test", inputs: {} }) });
    return r.status;
  }, slug);
  expect(await launch()).toBeLessThan(300);
  expect(await launch()).toBeLessThan(300);

  await page.goto(`/workflows/${slug}`);
  await tabLink(page, "Suivi");
  const picker = page.getByRole("region", { name: "Suivi des exécutions" });
  await expect(picker.getByText("2 en cours")).toBeVisible({ timeout: 20_000 });
  await expectNoA11yViolations(page);

  // vue agrégée : le compteur indique combien d'exécutions sont sur l'étape
  await picker.getByRole("button", { name: /Toutes les exécutions en cours \(2\)/ }).click();
  await expect(page.getByText(/2 exécution\(s\) en cours/)).toBeVisible();
  await expect(page.locator(".flow-count")).toContainText("2", { timeout: 25_000 }); // les deux exécutions doivent avoir atteint l'étape (machine chargée)
  await expectNoA11yViolations(page);

  // une seule exécution : état de chaque étape sur le schéma, avancement, détail
  await picker.getByRole("button", { name: /v1\.0\.0/ }).first().click();
  await expect(page.getByText(/0\/1 étape\(s\) terminée\(s\)/)).toBeVisible();
  await expect(page.locator(".flow-node.state-waiting, .flow-node.state-running").first()).toContainText("Pause");
  await expect(page.getByRole("link", { name: /Détail complet/ })).toBeVisible();
  await expectNoA11yViolations(page);
  await page.getByRole("button", { name: "Annuler l'exécution" }).click();
  await page.getByRole("dialog", { name: /Annuler l'exécution de/ }).getByRole("button", { name: "Annuler l'exécution" }).click();
  await expect(page.getByText("L'exécution a été annulée.")).toBeVisible({ timeout: 30_000 });

  // la seconde exécution continue indépendamment
  await expect(picker.getByText("1 en cours")).toBeVisible({ timeout: 20_000 });
  await tabLink(page, "Conception"); // le schéma redevient une vue d'édition, sans suivi
  await expect(page.getByRole("region", { name: "Suivi des exécutions" })).toHaveCount(0);
});

test("le bouton Publier de la barre d'actions ouvre la fenêtre de publication (sans changer d'onglet)", async ({ page }) => {
  await login(page);
  await page.goto("/workflows/redemarrer-vms/schema");
  await page.getByRole("button", { name: "Publier", exact: true }).click();
  const dlg = page.getByRole("dialog", { name: "Publier une version" });
  await expect(dlg).toBeVisible();
  await expect(dlg.getByRole("radio", { name: /Évolution/ })).toBeChecked();
  await expect(dlg.getByRole("radio", { name: /Correction/ })).toBeVisible();
  await expect(dlg.getByRole("radio", { name: /Changement important/ })).toBeVisible();
  await expectNoA11yViolations(page);
  await expect(page).toHaveURL(/\/schema$/);
  await dlg.getByRole("button", { name: "Annuler" }).click();
  await expect(dlg).toHaveCount(0);
});

test("publier depuis la fenêtre : la version est créée et annoncée", async ({ page }) => {
  await login(page);
  const slug = `pub-${Date.now().toString().slice(-6)}`;
  expect(await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [], nodes: [{ id: "a", type: "wait", name: "Pause", seconds: 1 }] })).toBe(201);
  await page.goto(`/workflows/${slug}/schema`);
  await page.getByRole("button", { name: "Publier", exact: true }).click();
  const dlg = page.getByRole("dialog", { name: "Publier une version" });
  await dlg.getByRole("radio", { name: /Correction/ }).check();
  await dlg.getByLabel("Note de version (facultatif)").fill("depuis la fenêtre");
  await dlg.getByRole("button", { name: "Publier", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: /Version 1\.0\.1 publiée/ })).toBeVisible();
  await expect(dlg).toHaveCount(0);
});

for (const size of [{ width: 1280, height: 720 }, { width: 1440, height: 900 }, { width: 1920, height: 1080 }]) {
  test(`l'éditeur de schéma tient dans la fenêtre ${size.width}×${size.height} (panneau ouvert, sans défilement de la page)`, async ({ page }) => {
    await page.setViewportSize(size);
    await login(page);
    await page.goto("/workflows/redemarrer-vms/schema");
    await page.locator(".flow-node").first().click();
    await expect(page.getByRole("complementary").filter({ hasText: "Reboot_script" }).first()).toBeVisible();
    const m = await page.evaluate(() => ({ doc: document.documentElement.scrollHeight, win: window.innerHeight, grid: document.querySelector(".schema-grid")?.getBoundingClientRect().bottom ?? 0 }));
    expect(m.doc).toBeLessThanOrEqual(m.win + 1);
    expect(m.grid).toBeLessThanOrEqual(m.win);
    await page.screenshot({ path: `test-results/editor-${size.width}x${size.height}.png` });
  });
}

test("déplacer un nœud : il suit la souris pendant le glissement, la position est enregistrée en un seul pas d'historique", async ({ page }) => {
  await login(page);
  const slug = `drag-${uniq()}`;
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [{ from: "a", to: "b" }],
    nodes: [{ id: "a", type: "wait", name: "Pause A", seconds: 1, ui: { x: 0, y: 0 } }, { id: "b", type: "wait", name: "Pause B", seconds: 1, ui: { x: 260, y: 0 } }] });
  await page.goto(`/workflows/${slug}/schema`);
  const box = page.locator(".flow-node", { hasText: "Pause A" }).locator(".flow-box");
  await expect(box).toBeVisible();
  const start = (await boxOf(box));
  const [cx, cy] = [start.x + start.width / 2, start.y + start.height / 2];
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx + 60, cy + 30, { steps: 6 });
  const mid = (await boxOf(box)); // la souris est TOUJOURS enfoncée
  expect(mid.x - start.x).toBeGreaterThan(40);
  expect(mid.y - start.y).toBeGreaterThan(15);
  await page.mouse.move(cx + 140, cy + 90, { steps: 6 });
  const late = (await boxOf(box));
  expect(late.x - start.x).toBeGreaterThan(110);
  await page.mouse.up();
  const dropped = (await boxOf(box));
  expect(Math.abs(dropped.x - late.x)).toBeLessThan(6);
  expect(Math.abs(dropped.y - late.y)).toBeLessThan(6);

  // enregistré dans le brouillon
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 });
  const saved = await page.evaluate(async (s) => (await (await fetch(`/api/v1/workspaces/demo/workflows/${s}/`)).json()).draft_graph.nodes.find((n: { id: string }) => n.id === "a").ui, slug);
  expect(saved.x).toBeGreaterThan(60);
  expect(saved.y).toBeGreaterThan(30);

  // un seul pas d'historique : « Annuler » ramène le nœud à sa place d'origine
  await page.getByRole("button", { name: "Annuler", exact: true }).click();
  const undone = (await boxOf(box));
  expect(Math.abs(undone.x - start.x)).toBeLessThan(6);
  expect(Math.abs(undone.y - start.y)).toBeLessThan(6);
});

test("début et fin déplaçables, lignes orthogonales dont on glisse les segments (souris et clavier), enregistrées ; « Organiser automatiquement » remet les lignes en automatique", async ({ page }) => {
  await login(page);
  const slug = `route-${uniq()}`;
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [{ from: "a", to: "b" }],
    nodes: [{ id: "a", type: "wait", name: "Pause A", seconds: 1, ui: { x: 0, y: 0 } }, { id: "b", type: "wait", name: "Pause B", seconds: 1, ui: { x: 400, y: -150 } }] });
  await page.goto(`/workflows/${slug}/schema`);
  const start = page.getByRole("img", { name: "Début du workflow" });
  await expect(start).toBeVisible();
  const before = (await boxOf(start));
  await page.mouse.move(before.x + 20, before.y + 20);
  await page.mouse.down();
  await page.mouse.move(before.x + 20, before.y + 140, { steps: 8 });
  await page.mouse.up();
  const after = (await boxOf(start));
  expect(after.y - before.y).toBeGreaterThan(100);

  // la ligne A → B : sélection, puis une poignée sur chacun de ses segments (vertical au milieu, horizontaux de départ et d'arrivée)
  const vertical = page.getByRole("button", { name: /Déplacer le segment vertical \d+ de la ligne vers « Pause B »/ });
  const horizontal = page.getByRole("button", { name: /Déplacer le segment horizontal \d+ de la ligne vers « Pause B »/ });
  await expect(async () => { // la sélection d'une ligne peut perdre la course avec l'ajustement de la vue : on réessaie
    await page.locator('.react-flow__edge[data-id="a->b"] .react-flow__edge-interaction').dispatchEvent("click");
    await expect(vertical).toHaveCount(1, { timeout: 2_000 });
  }).toPass({ timeout: 15_000 });
  await expect(horizontal).toHaveCount(2); // départ et arrivée : tous les segments se déplacent
  const read = () => page.evaluate(async (s) => (await (await fetch(`/api/v1/workspaces/demo/workflows/${s}/`)).json()).draft_graph, slug);
  const route = async () => (await read()).edges[0].ui?.route as number[] | undefined;

  // clavier, segment vertical : flèches gauche/droite (Maj : 1 pixel), le focus reste sur la poignée
  await vertical.focus();
  const x0 = (await boxOf(vertical)).x;
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Shift+ArrowRight");
  await expect(vertical).toBeFocused();
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 });
  await expect.poll(async () => (await route())?.length).toBe(1);
  const keyed = (await route())?.[0] as number;
  expect((await boxOf(vertical)).x - x0).toBeGreaterThan(10); // le segment a bougé de 8 + 8 + 1 unités du schéma, à l'écran aussi
  expect((await read()).layout.start.y).toBeGreaterThan(100);

  // souris, segment vertical : glisser la poignée vers la gauche
  const vb = await boxOf(vertical);
  await page.mouse.move(vb.x + vb.width / 2, vb.y + vb.height / 2);
  await page.mouse.down();
  await page.mouse.move(vb.x - 60, vb.y + 20, { steps: 8 });
  await page.mouse.up();
  await expect.poll(async () => (await route())?.[0]).toBeLessThan(keyed);
  expect((await read()).edges[0].ui.points).toBeUndefined();
  expect((await route())?.length).toBe(1); // le segment vertical seul : la ligne garde sa forme en Z

  // souris, segment horizontal d'arrivée : glisser vers le haut ajoute deux coudes et donne un détour [x, y, x]
  const hit = page.locator('.react-flow__edge[data-id="a->b"] .edge-seg-hit');
  await expect(hit).toHaveCount(3); // départ, vertical, arrivée : tous se prennent à la souris
  const arrival = hit.nth(2);
  await expect(arrival).toHaveCSS("cursor", "ns-resize");
  const ab = await boxOf(arrival);
  await page.mouse.move(ab.x + ab.width * 0.75, ab.y + ab.height / 2);
  await page.mouse.down();
  await page.mouse.move(ab.x + ab.width * 0.75, ab.y - 70, { steps: 10 });
  await page.mouse.up();
  await expect.poll(async () => (await route())?.length).toBe(3);
  const detour = (await route()) as number[];
  const targetY = (await read()).nodes.find((n: { id: string }) => n.id === "b").ui.y + 28; // l'icône est à mi-hauteur de l'étape
  expect(detour[1]).toBeLessThan(targetY - 40); // le segment horizontal du milieu est monté au-dessus de l'arrivée
  // souris, segment horizontal de départ : glisser vers le bas ajoute des coudes du côté de la source
  const departure = page.locator('.react-flow__edge[data-id="a->b"] .edge-seg-hit').first();
  await expect(departure).toHaveCSS("cursor", "ns-resize");

  await expectNoA11yViolations(page); // poignée visible : nom accessible, contraste, ordre de tabulation

  // Échap remet le tracé automatique
  await vertical.first().focus();
  await page.keyboard.press("Escape");
  await expect.poll(async () => (await read()).edges[0].ui).toBeUndefined();
  await menuClick(page, "Affichage", "Organiser automatiquement");
  await expect.poll(async () => (await read()).layout).toBeUndefined();
  await expectNoA11yViolations(page);
});

test("liens : les lignes partent et arrivent sur l'icône de l'étape (pas sur le bord de son libellé), et un lien se crée en glissant d'une sortie à une entrée", async ({ page }) => {
  await login(page);
  const slug = `liens-${uniq()}`;
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [{ from: "a", to: "b" }],
    nodes: [{ id: "a", type: "wait", name: "Pause A", seconds: 1, ui: { x: 0, y: 0 } }, { id: "b", type: "wait", name: "Pause B", seconds: 1, ui: { x: 360, y: 0 } }, { id: "c", type: "wait", name: "Pause C", seconds: 1, ui: { x: 360, y: 200 } }] });
  await page.goto(`/workflows/${slug}/schema`);
  await page.waitForSelector(".react-flow__edge");
  const read = () => page.evaluate(async (s) => (await (await fetch(`/api/v1/workspaces/demo/workflows/${s}/`)).json()).draft_graph, slug);

  // alignement : les points de connexion sont sur les bords de l'icône (carré de 56 px), à gauche et à droite, pas au bord du bloc icône + libellés ;
  // l'entrée et la sortie d'une même face sont légèrement écartées : face gauche, entrée en haut et sortie en bas ; face droite, l'inverse ; faces haut et bas, entrée à gauche et sortie à droite
  for (const id of ["a", "b"]) {
    const icon = await boxOf(page.locator(`.react-flow__node[data-id="${id}"] .flow-box`));
    const handle = (hid: string) => boxOf(page.locator(`.react-flow__node[data-id="${id}"] .react-flow__handle[data-handleid="${hid}"]`));
    const [inLeft, outLeft, inRight, outRight] = [await handle("i:l"), await handle("o:l"), await handle("i:r"), await handle("o:r")];
    const cy = icon.y + icon.height / 2;
    for (const h of [inLeft, outLeft, inRight, outRight]) expect(Math.abs(h.y + h.height / 2 - cy)).toBeLessThan(icon.height / 4); // dans la moitié centrale de la face
    expect(Math.abs(inLeft.x + inLeft.width / 2 - icon.x)).toBeLessThan(3); // le bord gauche de l'icône
    expect(Math.abs(outRight.x + outRight.width / 2 - (icon.x + icon.width))).toBeLessThan(3); // son bord droit
    expect(Math.abs(outLeft.y - inLeft.y)).toBeGreaterThan(5); // écartées, pas confondues
    expect(Math.abs(outRight.y - inRight.y)).toBeGreaterThan(5);
    expect(inLeft.y).toBeLessThan(outLeft.y); // face gauche : entrée (vert) au-dessus de la sortie (rouge)
    expect(outRight.y).toBeLessThan(inRight.y); // face droite : l'inverse, sortie au-dessus de l'entrée
    expect(Math.abs(outRight.y - inLeft.y)).toBeLessThan(2); // la sortie à droite et l'entrée à gauche de l'étape suivante sont à la même hauteur
  }

  // la ligne A → B touche vraiment l'icône de A (sortie) et celle de B (entrée)
  const pathBox = await boxOf(page.locator('.react-flow__edge[data-id="a->b"] .react-flow__edge-path'));
  const iconA = await boxOf(page.locator('.react-flow__node[data-id="a"] .flow-box'));
  const iconB = await boxOf(page.locator('.react-flow__node[data-id="b"] .flow-box'));
  expect(Math.abs(pathBox.x - (iconA.x + iconA.width))).toBeLessThan(6);
  expect(Math.abs(pathBox.x + pathBox.width - iconB.x)).toBeLessThan(10); // la flèche s'arrête au bord de l'icône d'arrivée

  // créer un lien A → C : glisser de la sortie de A jusqu'à l'icône de C ; la prévisualisation part bien de la poignée
  const out = await boxOf(page.locator('.react-flow__node[data-id="a"] .react-flow__handle[data-handleid="o:r"]'));
  const target = await boxOf(page.locator('.react-flow__node[data-id="c"] .react-flow__handle[data-handleid="i:l"]'));
  await page.mouse.move(out.x + out.width / 2, out.y + out.height / 2);
  await page.mouse.down();
  await page.mouse.move(target.x - 30, target.y + 10, { steps: 10 });
  const start = await page.locator(".react-flow__connection-path").evaluate((el) => { // premier point de la ligne tracée pendant le glisser, en pixels d'écran
    const path = el as unknown as SVGPathElement;
    const p = path.getPointAtLength(0);
    const m = path.getScreenCTM() as DOMMatrix;
    return { x: m.a * p.x + m.c * p.y + m.e, y: m.b * p.x + m.d * p.y + m.f };
  });
  expect(Math.abs(start.x - (out.x + out.width / 2))).toBeLessThan(3); // elle commence à la poignée (elle était décalée de 20 % par le zoom de l'interface)
  expect(Math.abs(start.y - (out.y + out.height / 2))).toBeLessThan(3);
  await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 6 });
  await page.mouse.up();
  await expect.poll(async () => (await read()).edges.some((e: { from: string; to: string }) => e.from === "a" && e.to === "c")).toBe(true);
  await expect(page.locator('.react-flow__edge[data-id="a->c"]')).toBeVisible();
  await expectNoA11yViolations(page);
  await page.evaluate(async (s) => { const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? ""; await fetch(`/api/v1/workspaces/demo/workflows/${s}/delete/`, { method: "POST", headers: { "X-CSRFToken": csrf } }); }, slug);
});

test("liens : quatre points de connexion par étape, faces choisies automatiquement et qui suivent les étapes, faces fixées en reliant, extrémité reprise", async ({ page }) => {
  await login(page);
  const slug = `faces-${uniq()}`;
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [{ from: "a", to: "b" }],
    nodes: [{ id: "a", type: "wait", name: "Pause A", seconds: 1, ui: { x: 100, y: 0 } }, { id: "b", type: "wait", name: "Pause B", seconds: 1, ui: { x: 500, y: 0 } }, { id: "c", type: "wait", name: "Pause C", seconds: 1, ui: { x: 100, y: 260 } }] });
  await page.goto(`/workflows/${slug}/schema`);
  await page.waitForSelector(".react-flow__edge");
  const read = () => page.evaluate(async (s) => (await (await fetch(`/api/v1/workspaces/demo/workflows/${s}/`)).json()).draft_graph, slug);
  const handle = (id: string, hid: string) => page.locator(`.react-flow__node[data-id="${id}"] .react-flow__handle[data-handleid="${hid}"]`);
  const centre = async (loc: ReturnType<typeof handle>) => { const b = await boxOf(loc); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; };
  // la face la plus proche du premier point de la ligne : celle d'où elle part (ou, pour la fin, celle où elle arrive)
  const faceOf = async (edge: string, node: string, end: "start" | "end") => {
    const p = await page.locator(`.react-flow__edge[data-id="${edge}"] .react-flow__edge-path`).evaluate((el, which) => {
      const path = el as unknown as SVGPathElement;
      const pt = path.getPointAtLength(which === "start" ? 0 : path.getTotalLength());
      const m = path.getScreenCTM() as DOMMatrix;
      return { x: m.a * pt.x + m.c * pt.y + m.e, y: m.b * pt.x + m.d * pt.y + m.f };
    }, end);
    let best = "";
    let dist = Number.POSITIVE_INFINITY;
    for (const f of ["t", "r", "b", "l"]) {
      const c = await centre(handle(node, `${end === "start" ? "o" : "i"}:${f}`));
      const d = Math.hypot(c.x - p.x, c.y - p.y);
      if (d < dist) { dist = d; best = f; }
    }
    return best;
  };

  // chaque étape a, par face, un point d'entrée et un point de sortie légèrement écartés, posés sur l'icône
  for (const id of ["a", "b", "c"]) {
    await expect(page.locator(`.react-flow__node[data-id="${id}"] .react-flow__handle`)).toHaveCount(8);
    const icon = await boxOf(page.locator(`.react-flow__node[data-id="${id}"] .flow-box`));
    const ti = await centre(handle(id, "i:t"));
    const to = await centre(handle(id, "o:t"));
    const bo = await centre(handle(id, "o:b"));
    expect(Math.abs(ti.y - icon.y)).toBeLessThan(3);
    expect(Math.abs(bo.y - (icon.y + icon.height))).toBeLessThan(3);
    expect(Math.abs((ti.x + to.x) / 2 - (icon.x + icon.width / 2))).toBeLessThan(3); // de part et d'autre du centre de la face
    expect(to.x - ti.x).toBeGreaterThan(5); // face du haut : entrée à gauche, sortie à droite
    const bi = await centre(handle(id, "i:b"));
    expect(bo.x - bi.x).toBeGreaterThan(5); // face du bas : de même
  }

  // automatique : B est à droite de A, la ligne part de la droite de A et arrive à gauche de B
  expect(await faceOf("a->b", "a", "start")).toBe("r");
  expect(await faceOf("a->b", "b", "end")).toBe("l");

  // elle suit l'étape quand on la déplace : B passe sous A, la ligne part du bas de A et arrive par le haut de B
  const na = await boxOf(page.locator('.react-flow__node[data-id="a"] .flow-box'));
  const nb = await boxOf(page.locator('.react-flow__node[data-id="b"] .flow-box'));
  await page.mouse.move(nb.x + nb.width / 2, nb.y + nb.height / 2);
  await page.mouse.down();
  await page.mouse.move(na.x + na.width / 2, nb.y + 120, { steps: 14 }); // à la verticale de A, plus bas
  await page.mouse.up();
  await expect.poll(() => faceOf("a->b", "a", "start")).toBe("b");
  expect(await faceOf("a->b", "b", "end")).toBe("t");
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 });
  expect((await read()).edges[0].ui).toBeUndefined(); // faces automatiques : rien n'est enregistré, elles se recalculent

  // relier en choisissant les faces : de la gauche de A à la droite de C ; elles sont gardées (le lien ne bouge plus de face)
  const from = await centre(handle("a", "o:l"));
  const to = await centre(handle("c", "i:r"));
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(to.x + 30, to.y, { steps: 12 });
  await page.mouse.move(to.x, to.y, { steps: 4 });
  await page.mouse.up();
  await expect.poll(async () => (await read()).edges.find((e: { from: string; to: string }) => e.from === "a" && e.to === "c")?.ui).toEqual({ sf: "l", tf: "r" });

  // reprendre une extrémité : on saisit le rond d'arrivée du lien sélectionné et on le dépose sur une autre face de la même étape
  await expect(async () => {
    await page.locator('.react-flow__edge[data-id="a->c"] .react-flow__edge-interaction').dispatchEvent("click");
    await expect(page.locator('.react-flow__edge[data-id="a->c"] .react-flow__edgeupdater-target')).toHaveCount(1, { timeout: 2_000 });
  }).toPass({ timeout: 15_000 });
  const anchor = await boxOf(page.locator('.react-flow__edge[data-id="a->c"] .react-flow__edgeupdater-target'));
  const top = await centre(handle("c", "i:t"));
  await page.mouse.move(anchor.x + anchor.width / 2, anchor.y + anchor.height / 2);
  await page.mouse.down();
  await page.mouse.move(top.x, top.y - 12, { steps: 12 });
  await page.mouse.move(top.x, top.y, { steps: 4 });
  await page.mouse.up();
  await expect.poll(async () => (await read()).edges.find((e: { from: string; to: string }) => e.from === "a" && e.to === "c")?.ui?.tf).toBe("t");
  expect(await faceOf("a->c", "c", "end")).toBe("t");
  await expectNoA11yViolations(page);
  await page.evaluate(async (s) => { const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? ""; await fetch(`/api/v1/workspaces/demo/workflows/${s}/delete/`, { method: "POST", headers: { "X-CSRFToken": csrf } }); }, slug);
});

test("liens : des étapes alignées (même à une fraction de pixel près) sont reliées par une ligne droite, sans arche ni bosse", async ({ page }) => {
  await login(page);
  const slug = `droit-${uniq()}`;
  const w = (id: string, x: number, y: number) => ({ id, type: "wait", name: `Pause ${id}`, seconds: 1, ui: { x, y } });
  // disposition réelle d'un workflow de la vitrine : la dernière étape et « Fin » sont alignées par leurs centres, à 0,2 pixel près
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, layout: { end: { x: 1292, y: 124 }, start: { x: -308, y: 123 } },
    nodes: [w("a", -29, 118), w("b", 610, 108), w("c", 856, 119)], edges: [{ from: "a", to: "b" }, { from: "b", to: "c" }] });
  await page.goto(`/workflows/${slug}/schema`);
  await page.waitForSelector(".react-flow__edge");
  const height = (edge: string) => page.locator(`.react-flow__edge[data-id="${edge}"] .react-flow__edge-path`).evaluate((el) => {
    const path = el as unknown as SVGPathElement;
    let lo = Number.POSITIVE_INFINITY;
    let hi = Number.NEGATIVE_INFINITY;
    for (let l = 0; l <= path.getTotalLength(); l += 1) { const p = path.getPointAtLength(l); lo = Math.min(lo, p.y); hi = Math.max(hi, p.y); }
    return hi - lo;
  });
  await expect.poll(() => height("c->e")).toBeLessThan(2); // la dernière étape vers « Fin » : droite
  await expect.poll(() => height("__start__->a".replace("__start__", "s"))).toBeLessThan(40); // le départ n'est pas une arche non plus
  expect(await height("b->c")).toBeLessThan(40);
  await page.evaluate(async (s) => { const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? ""; await fetch(`/api/v1/workspaces/demo/workflows/${s}/delete/`, { method: "POST", headers: { "X-CSRFToken": csrf } }); }, slug);
});

test("liens multiples : le point de jonction se déplace comme une étape, ses liens le suivent, on y relie de nouvelles sorties, on le supprime", async ({ page }) => {
  await login(page);
  const slug = `jonction-${uniq()}`;
  const w = (id: string, x: number, y: number) => ({ id, type: "wait", name: `Pause ${id}`, seconds: 1, ui: { x, y } });
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {},
    nodes: [w("m", 0, 160), w("a", 330, 0), w("b", 330, 160), w("c", 340, 330), w("d", 900, 170), { id: "j", type: "join", name: "Fichiers prêts", ui: { x: 650, y: 170 } }],
    edges: [{ from: "m", to: "a" }, { from: "m", to: "b" }, { from: "m", to: "c" }, { from: "a", to: "j" }, { from: "b", to: "j" }, { from: "c", to: "j" }, { from: "j", to: "d" }] });
  await page.goto(`/workflows/${slug}/schema`);
  await page.waitForSelector(".react-flow__edge");
  const read = () => page.evaluate(async (s) => (await (await fetch(`/api/v1/workspaces/demo/workflows/${s}/`)).json()).draft_graph, slug);
  const rings = page.locator(".react-flow__node-junction");

  // deux points de jonction apparaissent seuls : où les trois liens partent de la même étape, et où ils arrivent à la même jonction
  await expect(rings).toHaveCount(2);
  await expect(page.getByRole("img", { name: "Point de jonction : les liens partent d'ici" })).toHaveCount(1);
  await expect(page.getByRole("img", { name: "Point de jonction : les liens arrivent ici" })).toHaveCount(1);
  await expect(page.locator(".react-flow__node-junction .jin")).toHaveCount(2); // une entrée verte par point
  await expect(page.locator(".react-flow__node-junction .jout")).toHaveCount(6); // et trois sorties rouges, par point

  // la distance minimale entre un lien et le centre d'un point de jonction (en pixels d'écran) : un lien qui le traverse passe à l'entrée ou à une sortie du rond
  const distance = (edge: string, c: { x: number; y: number }) => page.locator(`.react-flow__edge[data-id="${edge}"] .react-flow__edge-path`).evaluate((el, ctr) => {
    const path = el as unknown as SVGPathElement;
    const m = path.getScreenCTM() as DOMMatrix;
    let best = Number.POSITIVE_INFINITY;
    for (let l = 0; l <= path.getTotalLength(); l += 1) { const p = path.getPointAtLength(l); best = Math.min(best, Math.hypot(m.a * p.x + m.c * p.y + m.e - ctr.x, m.b * p.x + m.d * p.y + m.f - ctr.y)); }
    return best;
  }, c);
  const centre = async (loc: ReturnType<typeof page.locator>) => { const b = await boxOf(loc); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; };
  const split = rings.filter({ has: page.locator(".jout") }).first();
  const before = await centre(split);

  // le déplacer : tous les liens qui partent de l'étape le suivent, et il devient un point posé (enregistré)
  await page.mouse.move(before.x, before.y);
  await page.mouse.down();
  await page.mouse.move(before.x + 70, before.y - 50, { steps: 10 });
  await page.mouse.up();
  await expect.poll(async () => (await read()).junctions?.length).toBe(1);
  const j = (await read()).junctions[0];
  expect(j).toMatchObject({ node: "m", role: "out", face: "r" });
  expect(j.x).toBeGreaterThan(60); // il a quitté sa place automatique
  const after = await centre(page.locator(".react-flow__node-junction").first());
  expect(after.x - before.x).toBeGreaterThan(40);
  for (const e of ["m->a", "m->b", "m->c"]) expect(await distance(e, after), e).toBeLessThan(12); // les trois liens passent par le point déplacé
  await expectNoA11yViolations(page);

  // on y relie une nouvelle sortie, comme on le ferait sur une étape : depuis la sortie rouge de droite vers l'entrée de l'étape d
  const out = await centre(page.locator(".react-flow__node-junction .jout[data-handleid=\"o:r\"]").first());
  const into = await centre(page.locator('.react-flow__node[data-id="d"] .react-flow__handle[data-handleid="i:l"]'));
  await page.mouse.move(out.x, out.y);
  await page.mouse.down();
  await page.mouse.move(into.x - 30, into.y, { steps: 12 });
  await page.mouse.move(into.x, into.y, { steps: 4 });
  await page.mouse.up();
  await expect.poll(async () => (await read()).edges.find((e: { from: string; to: string }) => e.from === "m" && e.to === "d")?.ui).toEqual({ sf: "r", tf: "l" }); // même étape, même point de connexion
  await expect.poll(async () => await distance("m->d", await centre(page.locator(".react-flow__node-junction").first()))).toBeLessThan(12); // il passe par le point de jonction

  // le supprimer : on le sélectionne, Suppr ; les liens reprennent leur tracé automatique et le point automatique revient
  await page.locator(".react-flow__node-junction").first().click({ force: true });
  await page.keyboard.press("Delete");
  await expect.poll(async () => (await read()).junctions).toBeUndefined();
  expect((await read()).edges.length).toBe(8); // les liens, eux, sont intacts (les sept d'origine et le nouveau)
  await expect(page.locator(".react-flow__node-junction")).toHaveCount(1); // seul reste celui des trois liens qui arrivent à la jonction : le lien m → d a ses deux extrémités partagées, donc pas de tronc commun côté étape m
  await page.evaluate(async (s) => { const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? ""; await fetch(`/api/v1/workspaces/demo/workflows/${s}/delete/`, { method: "POST", headers: { "X-CSRFToken": csrf } }); }, slug);
});

test("point de jonction : le double-clic l'inverse (entrées vertes à gauche, en haut et en bas ; sortie rouge à droite), et un second double-clic le remet", async ({ page }) => {
  await login(page);
  const slug = `inverse-${uniq()}`;
  const w = (id: string, x: number, y: number) => ({ id, type: "wait", name: `Pause ${id}`, seconds: 1, ui: { x, y } });
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {},
    nodes: [w("m", 0, 160), w("a", 330, 0), w("b", 330, 160), w("c", 340, 330)],
    edges: [{ from: "m", to: "a" }, { from: "m", to: "b" }, { from: "m", to: "c" }] });
  await page.goto(`/workflows/${slug}/schema`);
  await page.waitForSelector(".react-flow__edge");
  const read = () => page.evaluate(async (s) => (await (await fetch(`/api/v1/workspaces/demo/workflows/${s}/`)).json()).draft_graph, slug);
  const ring = page.locator(".react-flow__node-junction").first();
  const handle = (id: string) => ring.locator(`.react-flow__handle[data-handleid="${id}"]`);
  const where = async (id: string) => { const r = await boxOf(ring); const h = await boxOf(handle(id)); return { dx: Math.round(h.x + h.width / 2 - (r.x + r.width / 2)), dy: Math.round(h.y + h.height / 2 - (r.y + r.height / 2)) }; };

  // au départ : une entrée verte à gauche, trois sorties rouges (droite, haut, bas)
  expect(Math.sign((await where("i:l")).dx)).toBe(-1);
  await expect(ring.locator(".jin")).toHaveCount(1);
  await expect(ring.locator(".jout")).toHaveCount(3);
  for (const id of ["o:r", "o:t", "o:b"]) await expect(handle(id)).toHaveCount(1);

  // double-clic : le point automatique est posé là où il est, inversé : trois entrées vertes (gauche, haut, bas), une sortie rouge (droite)
  await ring.dblclick({ force: true });
  await expect.poll(async () => (await read()).junctions?.[0]?.flip).toBe(true);
  expect((await read()).junctions[0]).toMatchObject({ node: "m", role: "out", face: "r" });
  await expect(ring.locator(".jin")).toHaveCount(3);
  await expect(ring.locator(".jout")).toHaveCount(1);
  for (const id of ["i:l", "i:t", "i:b", "o:r"]) await expect(handle(id)).toHaveCount(1);
  expect(Math.sign((await where("i:l")).dx)).toBe(-1); // la gauche reste verte
  expect(Math.sign((await where("o:r")).dx)).toBe(1); // la droite reste rouge
  expect(Math.sign((await where("i:t")).dy)).toBe(-1); // le haut est devenu une entrée
  expect(Math.sign((await where("i:b")).dy)).toBe(1); // et le bas aussi
  await expect(handle("i:t")).toHaveClass(/jin/);
  await expect(handle("i:b")).toHaveClass(/jin/);
  await expect(handle("o:r")).toHaveClass(/jout/);
  await expect(page.getByRole("img", { name: /inversé : entrées à gauche, en haut et en bas, sortie à droite/ })).toHaveCount(1);
  // ses liens le traversent toujours : ils sont dessinés
  const reach = await page.locator('.react-flow__edge[data-id="m->a"] .react-flow__edge-path').evaluate((el) => (el as unknown as SVGPathElement).getTotalLength());
  expect(reach).toBeGreaterThan(50);
  await expectNoA11yViolations(page);

  // second double-clic : retour à la normale (la clé disparaît du graphe)
  await ring.dblclick({ force: true });
  await expect.poll(async () => (await read()).junctions?.[0]?.flip).toBeUndefined();
  await expect(ring.locator(".jin")).toHaveCount(1);
  await expect(ring.locator(".jout")).toHaveCount(3);

  // au clavier : le point sélectionné s'inverse avec Entrée
  await ring.click({ force: true });
  await page.keyboard.press("Enter");
  await expect.poll(async () => (await read()).junctions?.[0]?.flip).toBe(true);
  await page.evaluate(async (s) => { const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? ""; await fetch(`/api/v1/workspaces/demo/workflows/${s}/delete/`, { method: "POST", headers: { "X-CSRFToken": csrf } }); }, slug);
});

test("notes : toujours en arrière-plan, redimensionnables à la souris, couleur et opacité réglables", async ({ page }) => {
  await login(page);
  const slug = `notes-${uniq()}`;
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [{ from: "a", to: "b" }],
    nodes: [{ id: "a", type: "wait", name: "Pause A", seconds: 1, ui: { x: 100, y: 100 } }, { id: "b", type: "wait", name: "Pause B", seconds: 1, ui: { x: 420, y: 100 } }],
    notes: [{ id: "note_1", text: "Zone importante", ui: { x: 60, y: 60, w: 420, h: 150 } }] });
  await page.goto(`/workflows/${slug}/schema`);
  await page.waitForSelector(".react-flow__edge");
  const read = () => page.evaluate(async (s) => (await (await fetch(`/api/v1/workspaces/demo/workflows/${s}/`)).json()).draft_graph, slug);
  const note = page.locator('.react-flow__node[data-id="note_1"]');

  // arrière-plan : la note recouvre les deux étapes et la ligne, mais c'est l'étape (et la ligne) qui prend le clic, jamais la note
  const topAt = (x: number, y: number) => page.evaluate(([px, py]) => { const e = document.elementFromPoint(px as number, py as number); return e?.closest(".react-flow__node")?.getAttribute("data-id") ?? (e?.closest(".react-flow__edge") ? "edge" : e?.className?.toString() ?? ""); }, [x, y]);
  const iconA = await boxOf(page.locator('.react-flow__node[data-id="a"] .flow-box'));
  expect(await topAt(iconA.x + iconA.width / 2, iconA.y + iconA.height / 2)).toBe("a");
  await expect(note).toHaveCSS("z-index", "-1");
  await note.getByText("Zone importante").click(); // la sélectionner ne la fait pas passer devant
  await expect(note).toHaveClass(/selected/);
  await expect(note).toHaveCSS("z-index", "-1");
  expect(await topAt(iconA.x + iconA.width / 2, iconA.y + iconA.height / 2)).toBe("a");
  const line = await boxOf(page.locator('.react-flow__edge[data-id="a->b"] .react-flow__edge-path'));
  expect(await topAt(line.x + line.width / 2, line.y + line.height / 2)).toBe("edge");

  // redimensionnement à la souris : on tire la poignée du coin haut gauche (le coin bas droit peut passer sous le panneau des propriétés qui s'ouvre)
  const before = await boxOf(note);
  const grip = await boxOf(note.locator(".react-flow__resize-control.handle.top.left"));
  await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
  await page.mouse.down();
  await page.mouse.move(grip.x - 70, grip.y - 50, { steps: 10 });
  await page.mouse.up();
  const after = await boxOf(note);
  expect(after.width).toBeGreaterThan(before.width + 40);
  expect(after.height).toBeGreaterThan(before.height + 25);
  await expect.poll(async () => (await read()).notes[0].ui.w).toBeGreaterThan(420);
  const resized = (await read()).notes[0].ui;
  expect(resized.h).toBeGreaterThan(150);
  expect(resized.x).toBeLessThan(60); // la note a grandi vers le haut et la gauche : son coin haut gauche a bougé
  expect(resized.y).toBeLessThan(60);

  // couleur et opacité depuis les propriétés de la note
  const panel = page.getByRole("region", { name: "Note" });
  await panel.getByRole("radio", { name: "Bleu" }).click();
  await expect.poll(async () => (await read()).notes[0].color).toBe("#bfdbfe");
  await expect(note.locator(".flow-note")).toHaveCSS("background-color", "rgb(191, 219, 254)");
  await panel.getByLabel(/Opacité du fond/).fill("40");
  await expect.poll(async () => (await read()).notes[0].opacity).toBe(0.4);
  await expect(note.locator(".flow-note")).toHaveCSS("background-color", "rgba(191, 219, 254, 0.4)");

  // taille au clavier (équivalent des poignées) ; la position et le style sont gardés
  await panel.getByLabel("Largeur").fill("300");
  await expect.poll(async () => (await read()).notes[0].ui.w).toBe(300);
  expect((await read()).notes[0].color).toBe("#bfdbfe");
  expect((await read()).notes[0].ui.x).toBe(resized.x); // la position est gardée

  // déplacer la note garde sa taille
  const nb = await boxOf(note);
  await page.mouse.move(nb.x + 40, nb.y + 20);
  await page.mouse.down();
  await page.mouse.move(nb.x + 40, nb.y - 60, { steps: 8 });
  await page.mouse.up();
  await expect.poll(async () => (await read()).notes[0].ui.y).toBeLessThan(resized.y);
  expect((await read()).notes[0].ui.w).toBe(300);
  await expectNoA11yViolations(page);
  await page.evaluate(async (s) => { const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? ""; await fetch(`/api/v1/workspaces/demo/workflows/${s}/delete/`, { method: "POST", headers: { "X-CSRFToken": csrf } }); }, slug);
});

test("aimant : l'icône sous les commandes de zoom cale les étapes et les segments des lignes sur la grille, et la préférence est gardée", async ({ page }) => {
  await login(page);
  const slug = `aimant-${uniq()}`;
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [{ from: "a", to: "b" }],
    nodes: [{ id: "a", type: "wait", name: "Pause A", seconds: 1, ui: { x: 3, y: 5 } }, { id: "b", type: "wait", name: "Pause B", seconds: 1, ui: { x: 411, y: 203 } }] });
  await page.goto(`/workflows/${slug}/schema`);
  await page.waitForSelector(".react-flow__edge");
  const read = () => page.evaluate(async (s) => (await (await fetch(`/api/v1/workspaces/demo/workflows/${s}/`)).json()).draft_graph, slug);
  const magnet = page.getByRole("button", { name: "Aimanter à la grille" });

  // l'icône est dans les commandes du schéma, éteinte au départ
  await expect(magnet).toBeVisible();
  await expect(magnet).toHaveAttribute("aria-pressed", "false");
  expect((await boxOf(magnet)).y).toBeGreaterThan((await boxOf(page.locator(".react-flow__controls-zoomin"))).y); // sous le zoom
  await expectNoA11yViolations(page);

  // éteint : une étape se pose où on la dépose (position libre)
  const nodeA = page.locator('.react-flow__node[data-id="a"] .flow-box');
  const drag = async (dx: number, dy: number) => {
    const b = await boxOf(nodeA);
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
    await page.mouse.down();
    await page.mouse.move(b.x + b.width / 2 + dx, b.y + b.height / 2 + dy, { steps: 8 });
    await page.mouse.up();
  };
  await drag(53, 37);
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 });
  const free = (await read()).nodes.find((n: { id: string }) => n.id === "a").ui;
  expect(free.x % 16 !== 0 || free.y % 16 !== 0).toBe(true);

  // allumé : l'étape se cale sur la grille de 16 pixels
  await magnet.click();
  await expect(magnet).toHaveAttribute("aria-pressed", "true");
  await drag(41, 29);
  await expect.poll(async () => { const u = (await read()).nodes.find((n: { id: string }) => n.id === "a").ui; return [u.x % 16, u.y % 16]; }).toEqual([0, 0]);

  // allumé : un segment de ligne se cale aussi
  await expect(async () => {
    await page.locator('.react-flow__edge[data-id="a->b"] .react-flow__edge-interaction').dispatchEvent("click");
    await expect(page.getByRole("button", { name: /Déplacer le segment vertical \d+ de la ligne vers « Pause B »/ })).toHaveCount(1, { timeout: 2_000 });
  }).toPass({ timeout: 15_000 });
  const vertical = page.getByRole("button", { name: /Déplacer le segment vertical \d+ de la ligne vers « Pause B »/ });
  const vb = await boxOf(vertical);
  await page.mouse.move(vb.x + vb.width / 2, vb.y + vb.height / 2);
  await page.mouse.down();
  await page.mouse.move(vb.x + vb.width / 2 + 37, vb.y + vb.height / 2 + 5, { steps: 8 });
  await page.mouse.up();
  await expect.poll(async () => { const r = (await read()).edges[0].ui?.route; return r ? r[0] % 16 : -1; }).toBe(0);

  // la préférence est gardée au rechargement ; la vue d'une exécution n'a pas d'aimant (rien à déplacer)
  await page.reload();
  await page.waitForSelector(".react-flow__edge");
  await expect(page.getByRole("button", { name: "Aimanter à la grille" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Aimanter à la grille" }).click();
  await expect(page.getByRole("button", { name: "Aimanter à la grille" })).toHaveAttribute("aria-pressed", "false");
  await page.evaluate(async (s) => { const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? ""; await fetch(`/api/v1/workspaces/demo/workflows/${s}/delete/`, { method: "POST", headers: { "X-CSRFToken": csrf } }); }, slug);
});

test("brouillon sans positions : déplacer un nœud ne réorganise pas les autres, et la disposition est conservée au retour", async ({ page }) => {
  await login(page);
  const slug = `nopos-${uniq()}`;
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [{ from: "a", to: "b" }, { from: "b", to: "c" }],
    nodes: [{ id: "a", type: "wait", name: "Pause A", seconds: 1 }, { id: "b", type: "wait", name: "Pause B", seconds: 1 }, { id: "c", type: "wait", name: "Pause C", seconds: 1 }] });
  await page.goto(`/workflows/${slug}/schema`);
  const box = (name: string) => page.locator(".flow-node", { hasText: name }).locator(".flow-box");
  const at = async (name: string) => (await boxOf(box(name)));
  await expect(box("Pause C")).toBeVisible();
  const before = { a: await at("Pause A"), b: await at("Pause B"), c: await at("Pause C") };
  const [cx, cy] = [before.b.x + before.b.width / 2, before.b.y + before.b.height / 2];
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx, cy + 120, { steps: 8 });
  await page.mouse.up();
  const near = (p: { x: number; y: number }, q: { x: number; y: number }) => Math.abs(p.x - q.x) < 4 && Math.abs(p.y - q.y) < 4;
  expect(near(await at("Pause A"), before.a)).toBe(true); // les autres ne bougent pas
  expect(near(await at("Pause C"), before.c)).toBe(true);
  const moved = await at("Pause B");
  expect(moved.y - before.b.y).toBeGreaterThan(80);
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 });
  const saved = await page.evaluate(async (s) => (await (await fetch(`/api/v1/workspaces/demo/workflows/${s}/`)).json()).draft_graph.nodes.map((n: { ui?: unknown }) => !!n.ui), slug);
  expect(saved).toEqual([true, true, true]); // une position enregistrée pour CHAQUE nœud

  // sortir puis revenir : même disposition
  await goNav(page, "Workflows");
  await page.goto(`/workflows/${slug}/schema`);
  await expect(box("Pause C")).toBeVisible();
  // la vue s'ajuste à la fenêtre (zoom différent) : on compare les proportions de la disposition, pas les pixels
  const shape = async () => {
    const [a, b, c] = [await at("Pause A"), await at("Pause B"), await at("Pause C")];
    return { down: (b.y - a.y) / (c.x - a.x), right: (b.x - a.x) / (c.x - a.x) };
  };
  const now = await shape();
  expect(Math.abs(now.down - (moved.y - before.a.y) / (before.c.x - before.a.x))).toBeLessThan(0.05);
  expect(Math.abs(now.right - (moved.x - before.a.x) / (before.c.x - before.a.x))).toBeLessThan(0.05);
});

test("« Aller à… » centre la vue sur l'étape choisie, même très éloignée", async ({ page }) => {
  await login(page);
  const slug = `goto-${uniq()}`;
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [{ from: "a", to: "z" }],
    nodes: [{ id: "a", type: "wait", name: "Début lointain", seconds: 1, ui: { x: 0, y: 0 } }, { id: "z", type: "wait", name: "Étape très loin", seconds: 1, ui: { x: 4000, y: 900 } }] });
  await page.goto(`/workflows/${slug}/schema`);
  await expect(page.locator(".flow-node").first()).toBeVisible();
  await page.getByRole("button", { name: "Aller à", exact: false }).click();
  await page.getByRole("dialog", { name: "Aller à une étape" }).getByRole("button", { name: /Étape très loin/ }).click();
  const target = page.locator(".flow-node", { hasText: "Étape très loin" });
  await expect(target).toBeVisible();
  await expect.poll(async () => {
    const [n, c] = [await target.boundingBox(), await page.locator(".schema-center").boundingBox()];
    return n && c ? Math.round(Math.hypot(n.x + n.width / 2 - (c.x + c.width / 2), n.y + n.height / 2 - (c.y + c.height / 2))) : 9999;
  }, { timeout: 5000 }).toBeLessThan(40); // le nœud est au milieu du schéma
});

test.describe("éditeur : panneaux gauche et droit", () => {
  const open = async (page: Page) => {
    await login(page);
    await page.evaluate(() => localStorage.removeItem("orch.schema.panes"));
    const slug = `panes-${uniq()}`;
    await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [], nodes: [{ id: "a", type: "wait", name: "Pause", seconds: 1, ui: { x: 0, y: 0 } }] });
    await page.goto(`/workflows/${slug}/schema`);
    await expect(page.locator(".flow-node")).toBeVisible();
    return slug;
  };
  const width = async (loc: ReturnType<Page["locator"]>) => Math.round((await boxOf(loc)).width);

  test("des boutons masquent et affichent la palette et les propriétés", async ({ page }) => {
    await open(page);
    const palette = page.getByRole("complementary", { name: "Palette d'éléments" });
    const canvas = page.locator(".schema-center");
    await expect(palette).toBeVisible();
    const before = await width(canvas);
    const toggle = page.getByRole("button", { name: "Palette d'éléments", exact: true });
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
    await toggle.click();
    await expect(palette).toHaveCount(0);
    await expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(await width(canvas)).toBeGreaterThan(before + 150); // l'espace libéré va au schéma
    await toggle.click();
    await expect(palette).toBeVisible();

    // choisir une étape ne replie jamais la palette : seul l'utilisateur décide
    await page.locator(".flow-node").click();
    await expect(palette).toBeVisible();
    const props = page.getByRole("complementary", { name: "Propriétés de l'étape" });
    await expect(props).toBeVisible();
    await menuClick(page, "Affichage", "Propriétés");
    await expect(props).toHaveCount(0);
    await menuClick(page, "Affichage", "Propriétés");
    await expect(props).toBeVisible();
    await expectNoA11yViolations(page);
  });

  test("redimensionner à la souris et au clavier, mémorisé, double-clic pour revenir à la largeur par défaut", async ({ page }) => {
    await open(page);
    const palette = page.getByRole("complementary", { name: "Palette d'éléments" });
    const handle = page.getByRole("separator", { name: "Redimensionner la palette" });
    const start = await width(palette);
    expect(start).toBe(236);
    const hb = (await boxOf(handle));
    const [x, y] = [hb.x + hb.width / 2, hb.y + hb.height / 2];
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + 50, y, { steps: 5 });
    expect(await width(palette)).toBeGreaterThan(start + 35); // la largeur suit le pointeur AVANT le relâchement
    await page.mouse.move(x + 90, y, { steps: 5 });
    await page.mouse.up();
    const dragged = await width(palette);
    expect(dragged).toBeGreaterThan(start + 70);
    expect(dragged).toBeLessThanOrEqual(420);

    await handle.focus();
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    expect(await width(palette)).toBe(dragged + 32);
    await page.keyboard.press("ArrowLeft");
    expect(await width(palette)).toBe(dragged + 16);
    await expect(handle).toHaveAttribute("aria-valuenow", String(dragged + 16));

    await page.reload(); // mémorisé
    await expect(page.locator(".flow-node")).toBeVisible();
    expect(await width(palette)).toBe(dragged + 16);

    await handle.dblclick(); // largeur par défaut
    expect(await width(palette)).toBe(236);

    // limites : jamais en dessous du minimum
    await handle.focus();
    for (let i = 0; i < 12; i++) await page.keyboard.press("Shift+ArrowLeft");
    expect(await width(palette)).toBe(160);
  });

  test("panneau de droite : redimensionnable aussi, glisser vers la gauche l'élargit", async ({ page }) => {
    await open(page);
    await page.locator(".flow-node").click();
    const props = page.getByRole("complementary", { name: "Propriétés de l'étape" });
    const handle = page.getByRole("separator", { name: "Redimensionner les propriétés" });
    const start = await width(props);
    const hb = (await boxOf(handle));
    const [x, y] = [hb.x + hb.width / 2, hb.y + hb.height / 2];
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x - 80, y, { steps: 6 });
    expect(await width(props)).toBeGreaterThan(start + 60);
    await page.mouse.up();
    await handle.focus();
    const w = await width(props);
    await page.keyboard.press("ArrowLeft"); // vers la gauche : le panneau de droite s'élargit
    expect(await width(props)).toBe(w + 16);
  });
});

test("concepteur : choix multiple — cas ajoutés, sorties à relier, accessibilité", async ({ page }) => {
  await login(page);
  await createWorkflow(page, `Choix E2E ${uniq()}`);
  await tabLink(page, "Conception");
  await addTile(page, "Choix multiple");
  await panelTab(page, "Cas");
  await page.getByLabel("Condition du cas 1").fill("inputs.env == 'prod'");
  await page.getByLabel("Identifiant du cas 1").fill("prod");
  await page.getByRole("button", { name: "Ajouter un cas" }).click();
  await expect(page.getByLabel("Condition du cas 3")).toBeVisible();
  await page.getByRole("button", { name: "Retirer le cas 3" }).click();
  await expect(page.getByLabel("Condition du cas 3")).toHaveCount(0);
  // le cas 2 n'a pas de condition : le contrôle du poste le signale
  await panelTab(page, "Général");
  await expect(page.getByRole("list", { name: "Problèmes de cette étape" })).toContainText("n'a pas de condition");
  await expectNoA11yViolations(page);
});

test("concepteur : requête HTTP — mode test par défaut, réponse lisible par les étapes suivantes, accessibilité", async ({ page }) => {
  await login(page);
  await createWorkflow(page, `Http E2E ${uniq()}`);
  await tabLink(page, "Conception");
  await addTile(page, "Requête HTTP");
  await panelTab(page, "Requête");
  await expect(page.getByLabel("Mode")).toHaveValue("test");
  await page.getByLabel("Chemin").fill("/api/v1/machines/{{ inputs.id }}");
  await page.getByLabel("Méthode").selectOption("POST");
  await page.getByLabel("Corps (JSON ou texte)").fill("{ pas du json");
  await expect(page.getByRole("alert")).toContainText("JSON invalide");
  await page.getByLabel("Corps (JSON ou texte)").fill('{"nom": "{{ inputs.nom }}"}');
  await expect(page.getByRole("alert")).toHaveCount(0);
  await panelTab(page, "Général");
  await expect(page.getByRole("list", { name: "Problèmes de cette étape" })).toContainText("Choisissez la connexion");
  await expectNoA11yViolations(page);
});

test("concepteur : intégration Slack depuis la palette — opération, paramètres, mode test, accessibilité", async ({ page }) => {
  await login(page);
  await createWorkflow(page, `Slack E2E ${uniq()}`);
  await tabLink(page, "Conception");
  await page.getByLabel("Rechercher un élément").fill("slack");
  await addTile(page, "Slack");
  await panelTab(page, "Requête");
  await expect(page.getByLabel("Opération")).toHaveValue("post_message");
  await expect(page.getByLabel("Mode")).toHaveValue("test");
  await page.getByLabel("Canal (identifiant ou nom) *").fill("#exploitation");
  await page.getByLabel("Texte *").fill("Déploiement {{ inputs.version }} terminé");
  await page.getByLabel("Opération").selectOption("list_channels");
  await expect(page.getByLabel("Types")).toBeVisible();
  await expectNoA11yViolations(page);
});

test("concepteur : transformation de données — filtrer, regrouper, fusionner sans code, accessibilité", async ({ page }) => {
  await login(page);
  await createWorkflow(page, `Transformer E2E ${uniq()}`);
  await tabLink(page, "Conception");
  await addTile(page, "Transformer les données");
  await panelTab(page, "Transformation");
  await page.getByLabel("Liste à traiter").fill("inputs.lignes");
  await page.getByLabel("Condition").fill("item.montant > 100");
  await page.getByLabel("Opération").selectOption("group");
  await page.getByLabel("Regrouper selon").fill("item.service");
  await page.getByRole("button", { name: "Ajouter un calcul" }).click();
  await page.getByLabel("Valeur du calcul 1").fill("item.montant");
  await page.getByLabel("Opération").selectOption("merge");
  await expect(page.getByLabel("Seconde liste", { exact: true })).toBeVisible();
  await page.getByLabel("Mode").selectOption("append");
  await expect(page.getByLabel("Clé de la première liste")).toHaveCount(0);
  await expectNoA11yViolations(page);
});

test("concepteur : données d'une étape — aperçu d'expression, test, épinglage, accessibilité", async ({ page }) => {
  await login(page);
  await createWorkflow(page, `Données E2E ${uniq()}`);
  await tabLink(page, "Conception");
  await addTile(page, "Décision");
  await panelTab(page, "Décision");
  await page.getByLabel("Condition", { exact: true }).fill("1 + 1 == 2");
  await expect(page.getByText("Aperçu :")).toContainText("true");
  await panelTab(page, "Données");
  await expect(page.getByRole("button", { name: "Tester avec ces données" })).toBeVisible();
  await page.getByRole("button", { name: "Tester avec ces données" }).click();
  await expect(page.getByLabel("Résultat du test")).toContainText('"result": true');
  await page.getByRole("button", { name: "Saisir une donnée à épingler" }).click();
  await page.getByLabel("Sortie épinglée (JSON)").fill('{"result": false}');
  await page.getByRole("button", { name: "Enregistrer", exact: true }).click();
  await expect(page.getByLabel("Donnée épinglée")).toContainText('"result": false');
  await expectNoA11yViolations(page);
});

test("modèles : partir d'un modèle crée un brouillon prêt à configurer, accessibilité", async ({ page }) => {
  await login(page);
  await goNav(page, "Workflows");
  await page.getByRole("button", { name: "Partir d'un modèle" }).click();
  await expectNoA11yViolations(page);
  await page.getByRole("button", { name: "Utiliser le modèle Notifier un canal Slack" }).click();
  await expect(page).toHaveURL(/\/workflows\/notifier-un-canal-slack[^/]*\/schema/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Notifier un canal Slack");
  await page.getByRole("button", { name: "Aller à", exact: false }).click(); // sélecteur d'étapes : la liste contient les étapes du modèle
  await expect(page.getByRole("dialog", { name: "Aller à une étape" }).getByRole("button", { name: /Envoyer sur Slack/ })).toHaveCount(1);
});

test("concepteur : étape « Envoyer un e-mail » — connexion SMTP, mode test par défaut, accessibilité", async ({ page }) => {
  const id = uniq();
  await login(page);
  // la connexion SMTP (le secret n'est jamais renvoyé) : créée par l'API, comme le ferait l'assistant de connexion
  const status = await page.evaluate(async (id) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    const res = await fetch("/api/v1/workspaces/demo/connections/", {
      method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify({ name: `courrier-${id}`, label: `Courrier ${id}`, kind: "smtp", secret_value: "mot-de-passe-application",
        config: { host: "smtp.exemple.test", port: "465", tls: "ssl", username: "robot@exemple.test", from_address: "robot@exemple.test", from_name: "Fluxonata" } }),
    });
    return res.status;
  }, id);
  expect(status).toBe(201);
  await createWorkflow(page, `E-mail E2E ${id}`);
  await tabLink(page, "Conception");
  await addTile(page, "Envoyer un e-mail");
  await panelTab(page, "E-mail");
  await expect(page.getByLabel("Mode")).toHaveValue("test"); // jamais d'envoi réel sans choix explicite
  await page.getByLabel("Connexion (serveur SMTP)").selectOption({ label: `Courrier ${id}` });
  await expect(page.getByText(/Envoi depuis robot@exemple.test/)).toBeVisible();
  await page.getByLabel("Destinataire(s)").fill("lea@exemple.test");
  await page.getByLabel("Objet").fill("Panne : {{ inputs.objet }}");
  await page.getByLabel("Message (texte)").fill("Bonjour,\nLe portail est en panne.");
  await expect(page.getByLabel("Destinataire(s)")).toHaveValue("lea@exemple.test");
  await expectNoA11yViolations(page);
});

test("supprimer un lien ne supprime jamais les étapes qu'il relie", async ({ page }) => {
  await login(page);
  const slug = `lien-${uniq()}`;
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [{ from: "a", to: "b" }, { from: "b", to: "c" }],
    nodes: [{ id: "a", type: "wait", name: "Pause A", seconds: 1, ui: { x: 100, y: 100 } }, { id: "b", type: "wait", name: "Pause B", seconds: 1, ui: { x: 420, y: 100 } }, { id: "c", type: "wait", name: "Pause C", seconds: 1, ui: { x: 740, y: 100 } }] });
  await page.goto(`/workflows/${slug}/schema`);
  await page.waitForSelector(".react-flow__edge");
  const read = () => page.evaluate(async (s) => (await (await fetch(`/api/v1/workspaces/demo/workflows/${s}/`)).json()).draft_graph, slug);
  // une étape est sélectionnée, puis on clique un lien : Suppr ne retire que le lien
  await page.locator('.react-flow__node[data-id="b"] .flow-box').click();
  await page.locator('.react-flow__edge[data-id="a->b"] .react-flow__edge-path').click({ force: true });
  await page.keyboard.press("Delete");
  await expect.poll(async () => (await read()).edges.length).toBe(1);
  const g = await read();
  expect(g.nodes.map((n: { id: string }) => n.id).sort()).toEqual(["a", "b", "c"]);
  expect(g.edges).toEqual([expect.objectContaining({ from: "b", to: "c" })]);
  // l'étape B n'a plus d'entrée : elle reste, et on peut la reconnecter à la main
  await expect(page.locator('.react-flow__node[data-id="b"]')).toBeVisible();
  // aucun lien n'est recréé d'office vers « Début » : c'est à la personne de refaire le lien (et ce choix survit au rechargement)
  await expect(page.locator('.react-flow__edge[data-id="s->b"]')).toHaveCount(0);
  await expect(page.locator('.react-flow__edge[data-id="s->a"]')).toHaveCount(1);
  await expect.poll(async () => (await read()).detached?.start).toEqual(["b"]);
  await page.reload();
  await page.waitForSelector(".react-flow__edge");
  await expect(page.locator('.react-flow__edge[data-id="s->b"]')).toHaveCount(0);
});

test("liens avec « Début » et « Fin » : supprimer, puis les refaire à la souris", async ({ page }) => {
  await login(page);
  const slug = `debutfin-${uniq()}`;
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [{ from: "a", to: "b" }],
    nodes: [{ id: "a", type: "wait", name: "Pause A", seconds: 1, ui: { x: 300, y: 100 } }, { id: "b", type: "wait", name: "Pause B", seconds: 1, ui: { x: 620, y: 100 } }] });
  await page.goto(`/workflows/${slug}/schema`);
  await page.waitForSelector(".react-flow__edge");
  const read = () => page.evaluate(async (s) => (await (await fetch(`/api/v1/workspaces/demo/workflows/${s}/`)).json()).draft_graph, slug);
  const edge = (id: string) => page.locator(`.react-flow__edge[data-id="${id}"]`);
  const handle = (id: string, hid: string) => boxOf(page.locator(`.react-flow__node[data-id="${id}"] .react-flow__handle[data-handleid="${hid}"]`));
  const drag = async (from: { x: number; y: number; width: number; height: number }, to: { x: number; y: number; width: number; height: number }) => {
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 12 });
    await page.mouse.up();
  };
  await expect(edge("s->a")).toHaveCount(1);
  await expect(edge("b->e")).toHaveCount(1);

  // les liens « Début → A » et « B → Fin » se sélectionnent et se suppriment ; les étapes restent
  await edge("s->a").locator(".react-flow__edge-path").click({ force: true });
  await page.keyboard.press("Delete");
  await expect(edge("s->a")).toHaveCount(0);
  await edge("b->e").locator(".react-flow__edge-path").click({ force: true });
  await page.keyboard.press("Delete");
  await expect(edge("b->e")).toHaveCount(0);
  await expect.poll(async () => (await read()).detached).toEqual({ start: ["a"], end: ["b"] });
  expect((await read()).nodes).toHaveLength(2);

  // on les refait à la souris : de la sortie de « Début » vers l'entrée de A, de la sortie de B vers l'entrée de « Fin »
  await drag(await handle("__start__", "o:r"), await handle("a", "i:l"));
  await expect(edge("s->a")).toHaveCount(1);
  await expect.poll(async () => (await read()).detached?.start).toBeUndefined();
  await drag(await handle("b", "o:r"), await handle("__end__", "i:l"));
  await expect(edge("b->e")).toHaveCount(1);
  await expect.poll(async () => (await read()).detached).toBeUndefined();

  // « Début → B » est refusé : B a déjà une entrée (A → B)
  await drag(await handle("__start__", "o:r"), await handle("b", "i:l"));
  await expect(edge("s->b")).toHaveCount(0);
  expect((await read()).edges).toEqual([expect.objectContaining({ from: "a", to: "b" })]);
});

test("un lien presque droit devient droit : l'étape déplacée se cale sur l'alignement de celle à laquelle elle est reliée", async ({ page }) => {
  await login(page);
  const slug = `cale-${uniq()}`;
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [{ from: "a", to: "b" }, { from: "a", to: "c" }],
    nodes: [{ id: "a", type: "wait", name: "Pause A", seconds: 1, ui: { x: 100, y: 100 } }, { id: "b", type: "wait", name: "Pause B", seconds: 1, ui: { x: 420, y: 100 } }, { id: "c", type: "wait", name: "Pause C", seconds: 1, ui: { x: 420, y: 300 } }] });
  await page.goto(`/workflows/${slug}/schema`);
  await page.waitForSelector(".react-flow__edge");
  const icon = (id: string) => boxOf(page.locator(`.react-flow__node[data-id="${id}"] .flow-box`));
  const read = () => page.evaluate(async (s) => (await (await fetch(`/api/v1/workspaces/demo/workflows/${s}/`)).json()).draft_graph, slug);
  const dragBy = async (id: string, dx: number, dy: number) => {
    const b = await icon(id);
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
    await page.mouse.down();
    await page.mouse.move(b.x + b.width / 2 + dx, b.y + b.height / 2 + dy, { steps: 8 });
    await page.mouse.up();
  };
  // B est poussée de 6 pixels vers le bas : elle remonte se mettre à la hauteur de A, le lien est droit
  await dragBy("b", 0, 6);
  await expect.poll(async () => Math.abs((await icon("b")).y - (await icon("a")).y)).toBeLessThan(1);
  await expect.poll(async () => (await read()).nodes.find((n: { id: string }) => n.id === "b").ui.y).toBe(100);
  // C est tirée loin de l'alignement (200 pixels plus bas que A) : elle ne bouge pas toute seule
  const cBefore = await icon("c");
  await dragBy("c", 0, 40);
  const cAfter = await icon("c");
  expect(cAfter.y - cBefore.y).toBeGreaterThan(30); // elle a suivi la souris (à la grille près) et n'a pas été attirée vers A
  expect(cAfter.y - (await icon("a")).y).toBeGreaterThan(150);
});

test("plusieurs étapes se déplacent ensemble : Maj + clic ou Maj + glisser sur le fond, puis un seul glisser", async ({ page }) => {
  await login(page);
  const slug = `multi-${uniq()}`;
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [{ from: "a", to: "b" }, { from: "b", to: "c" }],
    nodes: [{ id: "a", type: "wait", name: "Pause A", seconds: 1, ui: { x: 100, y: 100 } }, { id: "b", type: "wait", name: "Pause B", seconds: 1, ui: { x: 420, y: 100 } }, { id: "c", type: "wait", name: "Pause C", seconds: 1, ui: { x: 740, y: 340 } }] });
  await page.goto(`/workflows/${slug}/schema`);
  await page.waitForSelector(".react-flow__edge");
  const ui = async () => page.evaluate(async (s) => Object.fromEntries((await (await fetch(`/api/v1/workspaces/demo/workflows/${s}/`)).json()).draft_graph.nodes.map((n: { id: string; ui: { x: number; y: number } }) => [n.id, n.ui])), slug);
  const icon = (id: string) => page.locator(`.react-flow__node[data-id="${id}"] .flow-box`);
  const center = async (id: string) => { const b = await boxOf(icon(id)); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; };

  // Maj + clic : A puis B sont sélectionnées (sans panneau de propriétés), on tire A : B suit avec le même décalage
  await icon("a").click();
  await icon("b").click({ modifiers: ["Shift"] });
  await expect(page.locator('.react-flow__node[data-id="a"] .flow-node.selected')).toHaveCount(1);
  await expect(page.locator(".react-flow__node .flow-node.selected")).toHaveCount(2);
  const before = await ui();
  const a = await center("a");
  await page.mouse.move(a.x, a.y);
  await page.mouse.down();
  await page.mouse.move(a.x + 60, a.y + 112, { steps: 10 });
  await page.mouse.up();
  await expect.poll(async () => (await ui()).b.y - before.b.y).toBeGreaterThan(80);
  const after = await ui();
  expect(Math.abs((after.a.x - before.a.x) - (after.b.x - before.b.x))).toBeLessThan(2); // même déplacement
  expect(Math.abs((after.a.y - before.a.y) - (after.b.y - before.b.y))).toBeLessThan(2);
  expect(after.c).toEqual(before.c); // C n'était pas sélectionnée

  // Maj + glisser sur le fond : un rectangle sélectionne toutes les étapes qu'il touche
  await page.mouse.click(5, 5); // (clic hors du canevas : rien)
  const box = await boxOf(page.locator(".react-flow__pane"));
  await page.keyboard.down("Shift");
  await page.mouse.move(box.x + 10, box.y + 10);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width - 10, box.y + box.height - 10, { steps: 10 });
  await page.mouse.up();
  await page.keyboard.up("Shift");
  await expect(page.locator(".react-flow__node .flow-node.selected")).toHaveCount(3);
});
