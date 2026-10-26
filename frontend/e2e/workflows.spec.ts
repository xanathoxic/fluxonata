import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, goNav, login, publishViaApi } from "./helpers";

const uniq = () => `${Date.now()}`.slice(-7);
const tabLink = (page: Page, name: string) => page.getByRole("navigation", { name: "Sections du workflow" }).getByRole("link", { name, exact: true }).click();
const addTile = (page: Page, label: string) => page.getByRole("button", { name: `Ajouter : ${label}`, exact: true }).click();
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
  await tabLink(page, "Schéma");
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
  await expect(page.getByText("✔ Brouillon enregistré")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator(".flow-node")).toHaveCount(3);
  await expectNoA11yViolations(page);
  await page.getByRole("button", { name: "Vue liste" }).click(); // même contenu, sans canevas
  await expect(page.getByRole("list", { name: "Étapes du workflow" }).locator("li")).toHaveCount(3);

  await tabLink(page, "Historique des versions");
  await page.getByRole("button", { name: "Publier une nouvelle version" }).click();
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

  await tabLink(page, "Schéma");
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
  await expect(page.getByText("✔ Brouillon enregistré")).toBeVisible({ timeout: 10_000 });
  await tabLink(page, "Historique des versions");
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
  await page.getByRole("button", { name: "Vue liste" }).focus();
  await page.keyboard.press("Enter");
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
  await page.getByRole("row", { name: /Redémarrer des VM/ }).getByRole("button", { name: /favoris/ }).click(); // état initial rétabli
  await expect(page.getByRole("row", { name: /Redémarrer des VM/ }).getByRole("button", { name: /Ajouter .* aux favoris/ })).toBeVisible();
});

test("assistant de lancement par étapes, schéma d'exécution coloré et journaux (exemple « Redémarrer des VM »)", async ({ page }) => {
  await login(page);
  await goNav(page, "Workflows");
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
  for (const name of ["Résumé", "Variables", "Entrées/Sorties", "Schéma", "Suivi", "Formulaire d'entrée", "Historique des versions", "Audit", "Exécutions"]) {
    await tabLink(page, name);
    await expect(page.getByRole("navigation", { name: "Sections du workflow" }).getByRole("link", { name, exact: true })).toHaveAttribute("aria-current", "page");
    await expectNoA11yViolations(page);
  }
  await page.getByRole("button", { name: "Rechercher les dépendances" }).click();
  await expect(page.getByRole("region", { name: "Dépendances" })).toBeVisible();
  await page.getByRole("button", { name: "Valider", exact: true }).click();
  await expect(page.getByText(/Le workflow est valide/)).toBeVisible({ timeout: 15_000 });
});

test("approbateur sur mobile : approuve une demande depuis une vue compréhensible", async ({ browser }) => {
  const marker = `E2E-${Date.now()}`;
  const requester = await (await browser.newContext()).newPage();
  await login(requester, "alice");
  await goNav(requester, "Workflows");
  await requester.getByRole("row", { name: /Analyse avec validation/ }).getByRole("link", { name: /Lancer/ }).click();
  await requester.getByLabel(/Texte à analyser/).fill(`un deux trois quatre ${marker}`);
  await requester.getByRole("button", { name: "Lancer l'automatisation" }).click();
  await expect(requester.getByText("Une approbation est attendue").first()).toBeVisible({ timeout: 30_000 });

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
  await viewer.getByRole("row", { name: /Analyse avec validation/ }).getByRole("link", { name: "Consulter" }).click();
  await expect(viewer.getByText("accès en lecture seule")).toBeVisible();
  await expect(viewer.getByRole("button", { name: "Supprimer" })).toHaveCount(0);
  await expect(viewer.getByRole("button", { name: "Annuler" })).toBeDisabled();
  await tabLink(viewer, "Historique des versions");
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
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: "Annuler l'exécution" }).click();
  await expect(page.getByText("L'exécution a été annulée.")).toBeVisible({ timeout: 30_000 });

  // la seconde exécution continue indépendamment
  await expect(picker.getByText("1 en cours")).toBeVisible({ timeout: 20_000 });
  await tabLink(page, "Schéma"); // le schéma redevient une vue d'édition, sans suivi
  await expect(page.getByRole("region", { name: "Suivi des exécutions" })).toHaveCount(0);
});

test("le bouton Publier de la barre d'actions ouvre le formulaire de publication", async ({ page }) => {
  await login(page);
  await page.goto("/workflows/redemarrer-vms/schema");
  await page.getByRole("link", { name: "Publier", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "Sections du workflow" }).getByRole("link", { name: "Historique des versions" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByLabel("Type de version")).toBeFocused();
  await expect(page.getByRole("button", { name: "Publier une nouvelle version" })).toBeVisible();
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
  await expect(page.getByText("✔ Brouillon enregistré")).toBeVisible({ timeout: 15_000 });
  const saved = await page.evaluate(async (s) => (await (await fetch(`/api/v1/workspaces/demo/workflows/${s}/`)).json()).draft_graph.nodes.find((n: { id: string }) => n.id === "a").ui, slug);
  expect(saved.x).toBeGreaterThan(60);
  expect(saved.y).toBeGreaterThan(30);

  // un seul pas d'historique : « Annuler » ramène le nœud à sa place d'origine
  await page.getByRole("button", { name: "Annuler", exact: true }).click();
  const undone = (await box.boundingBox())!;
  expect(Math.abs(undone.x - start.x)).toBeLessThan(6);
  expect(Math.abs(undone.y - start.y)).toBeLessThan(6);
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
    const toggle = page.getByRole("button", { name: "Palette", exact: true });
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
    await toggle.click();
    await expect(palette).toHaveCount(0);
    await expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(await width(canvas)).toBeGreaterThan(before + 150); // l'espace libéré va au schéma
    await toggle.click();
    await expect(palette).toBeVisible();

    await page.locator(".flow-node").click();
    const props = page.getByRole("complementary", { name: "Propriétés de l'étape" });
    await expect(props).toBeVisible();
    await page.getByRole("button", { name: "Propriétés", exact: true }).click();
    await expect(props).toHaveCount(0);
    await page.getByRole("button", { name: "Propriétés", exact: true }).click();
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
