import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, goNav, login, menuClick, publishViaApi } from "./helpers";

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
  await expect(page.getByRole("link", { name: "Redémarrer des VM", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Analyse avec validation", exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Infrastructure/ }).click();
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
  const start = (await box.boundingBox())!;
  const [cx, cy] = [start.x + start.width / 2, start.y + start.height / 2];
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx + 60, cy + 30, { steps: 6 });
  const mid = (await box.boundingBox())!; // la souris est TOUJOURS enfoncée
  expect(mid.x - start.x).toBeGreaterThan(40);
  expect(mid.y - start.y).toBeGreaterThan(15);
  await page.mouse.move(cx + 140, cy + 90, { steps: 6 });
  const late = (await box.boundingBox())!;
  expect(late.x - start.x).toBeGreaterThan(110);
  await page.mouse.up();
  const dropped = (await box.boundingBox())!;
  expect(Math.abs(dropped.x - late.x)).toBeLessThan(6);
  expect(Math.abs(dropped.y - late.y)).toBeLessThan(6);

  // enregistré dans le brouillon
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 });
  const saved = await page.evaluate(async (s) => (await (await fetch(`/api/v1/workspaces/demo/workflows/${s}/`)).json()).draft_graph.nodes.find((n: { id: string }) => n.id === "a").ui, slug);
  expect(saved.x).toBeGreaterThan(60);
  expect(saved.y).toBeGreaterThan(30);

  // un seul pas d'historique : « Annuler » ramène le nœud à sa place d'origine
  await page.getByRole("button", { name: "Annuler", exact: true }).click();
  const undone = (await box.boundingBox())!;
  expect(Math.abs(undone.x - start.x)).toBeLessThan(6);
  expect(Math.abs(undone.y - start.y)).toBeLessThan(6);
});

test("début et fin déplaçables, lignes ajustables au clavier, enregistrés ; « Organiser automatiquement » remet les lignes en automatique", async ({ page }) => {
  await login(page);
  const slug = `route-${uniq()}`;
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [{ from: "a", to: "b" }],
    nodes: [{ id: "a", type: "wait", name: "Pause A", seconds: 1, ui: { x: 0, y: 0 } }, { id: "b", type: "wait", name: "Pause B", seconds: 1, ui: { x: 400, y: 0 } }] });
  await page.goto(`/workflows/${slug}/schema`);
  const start = page.getByRole("img", { name: "Début du workflow" });
  await expect(start).toBeVisible();
  const before = (await start.boundingBox())!;
  await page.mouse.move(before.x + 20, before.y + 20);
  await page.mouse.down();
  await page.mouse.move(before.x + 20, before.y + 140, { steps: 8 });
  await page.mouse.up();
  const after = (await start.boundingBox())!;
  expect(after.y - before.y).toBeGreaterThan(100);

  // la ligne A → B : sélection, poignée, flèche droite (alternative au glissement)
  const handle = page.getByRole("button", { name: /Déplacer la ligne vers « Pause B »/ });
  await expect(async () => { // la sélection d'une ligne peut perdre la course avec l'ajustement de la vue : on réessaie
    await page.locator('.react-flow__edge[data-id="a->b"] .react-flow__edge-interaction').dispatchEvent("click");
    await expect(handle).toBeVisible({ timeout: 2_000 });
  }).toPass({ timeout: 15_000 });
  await handle.focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");

  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 });
  const read = () => page.evaluate(async (s) => (await (await fetch(`/api/v1/workspaces/demo/workflows/${s}/`)).json()).draft_graph, slug);
  const saved = await read();
  expect(saved.layout.start.y).toBeGreaterThan(100);
  expect(saved.edges[0].ui.cx).toBeGreaterThan(0);

  // Échap sur la poignée : routage automatique de nouveau
  await handle.focus();
  await page.keyboard.press("Escape");
  await expect.poll(async () => (await read()).edges[0].ui).toBeUndefined();
  await menuClick(page, "Affichage", "Organiser automatiquement");
  await expect.poll(async () => (await read()).layout).toBeUndefined();
  await expectNoA11yViolations(page);
});

test("brouillon sans positions : déplacer un nœud ne réorganise pas les autres, et la disposition est conservée au retour", async ({ page }) => {
  await login(page);
  const slug = `nopos-${uniq()}`;
  await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [{ from: "a", to: "b" }, { from: "b", to: "c" }],
    nodes: [{ id: "a", type: "wait", name: "Pause A", seconds: 1 }, { id: "b", type: "wait", name: "Pause B", seconds: 1 }, { id: "c", type: "wait", name: "Pause C", seconds: 1 }] });
  await page.goto(`/workflows/${slug}/schema`);
  const box = (name: string) => page.locator(".flow-node", { hasText: name }).locator(".flow-box");
  const at = async (name: string) => (await box(name).boundingBox())!;
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
  const width = async (loc: ReturnType<Page["locator"]>) => Math.round((await loc.boundingBox())!.width);

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
    const hb = (await handle.boundingBox())!;
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
    const hb = (await handle.boundingBox())!;
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
