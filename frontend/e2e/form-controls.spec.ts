import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, login } from "./helpers";

async function call(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ({ method, path, body }) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    const r = await fetch(`/api/v1/workspaces/demo${path}`, { method, headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: body ? JSON.stringify(body) : undefined });
    return { status: r.status, data: await r.json().catch(() => ({})) };
  }, { method, path, body });
}
const section = (page: Page, name: string) => page.getByRole("navigation", { name: "Sections du contrôle" }).getByRole("button", { name }).click();
const part = (key: string, label: string, extra: Record<string, unknown> = {}) => ({ key, label, type: "text", ...extra });

test("les formulaires et les contrôles sont rangés dans Bibliothèques", async ({ page }) => {
  await login(page);
  const side = page.getByRole("navigation", { name: "Navigation principale" });
  await expect(side.getByRole("link", { name: "Formulaires", exact: true })).toHaveCount(0); // plus d'entrée de premier niveau
  await side.getByRole("link", { name: "Bibliothèques" }).click();
  const sub = side.getByRole("list", { name: "Bibliothèques" });
  await expect(sub.getByRole("link")).toHaveText(["Actions", "Skills IA", "Formulaires", "Contrôles"]);
  await sub.getByRole("link", { name: "Formulaires" }).click();
  await expect(page).toHaveURL(/\/library\/forms$/);
  await expect(page.getByRole("heading", { level: 1, name: "Formulaires" })).toBeVisible();
  await sub.getByRole("link", { name: "Contrôles" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Contrôles" })).toBeVisible();
  // les anciens liens continuent de fonctionner
  await page.goto("/forms");
  await expect(page).toHaveURL(/\/library\/forms$/);
});

test("concepteur de contrôle : liste à cocher avec détails, aperçu, publication, puis palette de l'éditeur de formulaires", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const name = `Matériel ${id}`;
  await page.goto("/library/controls");
  await page.getByRole("button", { name: "Nouveau contrôle" }).click();
  const dlg = page.getByRole("dialog", { name: "Nouveau contrôle" });
  await dlg.getByLabel("Nom du contrôle").fill(name);
  await dlg.getByRole("radio", { name: /Liste de choix/ }).click();
  await dlg.getByRole("checkbox", { name: "Plusieurs choix possibles" }).check();
  await dlg.getByRole("button", { name: "Créer" }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();

  // trois cases dans la section « Options », au plus 2 cochées, icône dans « Général »
  await section(page, "Options");
  const od = page.locator(".cd-main");
  await od.getByLabel("Libellé de l'option").fill("Écran");
  await od.getByRole("button", { name: "Ajouter une option" }).click();
  await od.getByLabel("Libellé de l'option").fill("Clavier");
  await od.getByLabel("Valeur remise au workflow").fill("clavier");
  await od.getByRole("button", { name: "Ajouter une option" }).click();
  await od.getByLabel("Au plus coché").fill("2");
  await section(page, "Général");
  await page.getByRole("radio", { name: "cart" }).click();

  // un détail demandé pour chaque case cochée : une quantité (section « Modèle d'une option »)
  await section(page, "Modèle d'une option");
  await page.getByRole("button", { name: "Nombre", exact: true }).click();
  await page.getByLabel("Libellé", { exact: true }).fill("Quantité");
  await page.getByLabel("Clé du sous-champ").fill("qte");
  await expect(page.getByRole("status").filter({ hasText: "Valide" }).first()).toBeVisible();
  await expectNoA11yViolations(page);
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 }); // enregistrement automatique

  // aperçu : cocher ouvre le détail, la valeur produite est visible
  await page.getByRole("tablist", { name: "Contrôle" }).getByRole("tab", { name: "Aperçu" }).click();
  const prev = page.getByRole("region", { name: "Aperçu du contrôle" });
  await prev.getByRole("checkbox", { name: "Écran" }).check();
  await prev.getByLabel("Quantité").fill("3");
  await expect(prev.getByLabel("Valeur produite")).toContainText('"value": "option-1"');
  await expect(prev.getByLabel("Valeur produite")).toContainText('"qte": 3');
  await prev.getByRole("checkbox", { name: "Clavier" }).check();

  // publication
  await page.getByRole("button", { name: "Publier" }).click();
  await page.getByRole("dialog", { name: "Publier une version" }).getByRole("button", { name: "Publier" }).click();
  await expect(page.getByText("v1.0.0 publiée")).toBeVisible();

  // le contrôle apparaît dans la palette de l'éditeur de formulaires
  const slug = `ctl-${id}`;
  expect((await call(page, "POST", "/form-library/", { slug, name: `Formulaire ${id}` })).status).toBe(201);
  await page.goto(`/library/forms/${slug}`);
  const pal = page.getByRole("complementary", { name: "Palette de champs" });
  await expect(pal.getByRole("heading", { name: "Contrôles personnalisés" })).toBeVisible();
  await pal.getByRole("button", { name: name }).click();
  await expect(page.getByRole("button", { name: new RegExp(`^${name}`) }).first()).toBeVisible();
  await page.getByRole("tab", { name: "Aperçu" }).click();
  await page.getByRole("checkbox", { name: "Écran" }).check();
  await page.getByLabel("Quantité").fill("2");
  await expect(page.getByLabel("Quantité")).toHaveValue("2");
});

test("contrôle à lignes répétées : calcul par ligne, total du formulaire et refus côté serveur", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const spec = {
    version: 1, mode: "repeat", min: 1, max: 5, row_label: "Article", add_label: "Ajouter un article",
    parts: [
      part("produit", "Produit", { required: true, layout: { row: 1, col: 1, span: 6 } }),
      part("qte", "Quantité", { type: "number", required: true, min: 1, layout: { row: 1, col: 7, span: 3 } }),
      part("prix", "Prix", { type: "number", layout: { row: 1, col: 10, span: 3 } }),
      part("total", "Total ligne", { type: "number", compute: "{qte} * {prix}", layout: { row: 2, col: 1, span: 12 } }),
    ],
  };
  expect((await call(page, "POST", "/control-library/", { slug: `lignes-${id}`, name: `Lignes ${id}`, spec })).status).toBe(201);
  expect((await call(page, "POST", `/control-library/lignes-${id}/publish/`, {})).status).toBe(201);
  const control = (await call(page, "GET", "/control-library/?published=1")).data.results.find((c: { slug: string }) => c.slug === `lignes-${id}`);
  const def = { version: 1, sections: [{ id: "s1", title: "T", fields: [
    { key: "lignes", label: "Lignes de commande", type: "custom", layout: { row: 1, col: 1, span: 12 }, control: { ref: control.slug, version: control.published.version, name: control.published.name, icon: control.published.icon, spec: control.published.spec } },
    { key: "somme", label: "Somme", type: "number", compute: 'sum_col({lignes}, "total")', layout: { row: 2, col: 1, span: 6 } },
    { key: "nb", label: "Nombre d'articles", type: "number", compute: "count({lignes})", layout: { row: 2, col: 7, span: 6 } },
  ] }] };
  const slug = `cmd-${id}`;
  expect((await call(page, "POST", "/form-library/", { slug, name: `Commande ${id}`, definition: def })).status).toBe(201);
  await page.goto(`/library/forms/${slug}`);
  await page.getByRole("tab", { name: "Aperçu" }).click();
  const line1 = page.getByRole("group", { name: "Article 1" });
  await line1.getByLabel("Produit").fill("Stylo");
  await line1.getByLabel("Quantité").fill("2");
  await line1.getByLabel("Prix").fill("1.5");
  await expect(line1.getByLabel("Total ligne")).toHaveValue("3");
  await page.getByRole("button", { name: "Ajouter un article" }).click();
  const line2 = page.getByRole("group", { name: "Article 2" });
  await line2.getByLabel("Produit").fill("Cahier");
  await line2.getByLabel("Quantité").fill("1");
  await line2.getByLabel("Prix").fill("4");
  await expect(page.getByLabel("Somme")).toHaveValue("7");
  await expect(page.getByLabel("Nombre d'articles")).toHaveValue("2");
  await line2.getByRole("button", { name: "Supprimer article 2" }).click();
  await expect(page.getByLabel("Somme")).toHaveValue("3");

  // le serveur refuse un contrôle dont une sous-règle n'est pas respectée
  const bad = JSON.parse(JSON.stringify(def)) as typeof def;
  (bad.sections[0]!.fields[0] as { control: { spec: { parts: unknown[] } } }).control.spec.parts.push({ key: "x", label: "X", type: "datagrid" });
  expect((await call(page, "POST", "/form-library/", { slug: `${slug}-bad`, name: "Refusé", definition: bad })).status).toBe(400);
});

test("liste de choix : toutes les options reprennent le même modèle de champs ; tableau à colonne déroulante et total", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const name = `Contact ${id}`;
  await page.goto("/library/controls");
  await page.getByRole("button", { name: "Nouveau contrôle" }).click();
  const dlg = page.getByRole("dialog", { name: "Nouveau contrôle" });
  await dlg.getByLabel("Nom du contrôle").fill(name);
  await dlg.getByRole("radio", { name: /Liste de choix/ }).click();
  await dlg.getByRole("button", { name: "Créer" }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();

  // deux options ; le modèle (identique pour toutes) demande une adresse e-mail obligatoire
  await section(page, "Options");
  const od = page.locator(".cd-main");
  await od.getByLabel("Libellé de l'option").fill("E-mail");
  await od.getByLabel("Valeur remise au workflow").fill("mail");
  await od.getByRole("button", { name: "Ajouter une option" }).click();
  await od.getByLabel("Libellé de l'option").fill("Autre");
  await od.getByLabel("Valeur remise au workflow").fill("autre");
  await expect(od.getByText("champ(s) qui lui sont propres")).toHaveCount(0);
  await section(page, "Modèle d'une option");
  await expect(od.getByText("ouvre ces champs enfants")).toBeVisible();
  await od.getByRole("button", { name: "Adresse e-mail", exact: true }).click();
  await od.getByLabel("Clé du sous-champ").fill("adresse");
  await od.getByLabel("Obligatoire").check();
  await expectNoA11yViolations(page);
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 }); // enregistrement automatique

  await page.getByRole("tablist", { name: "Contrôle" }).getByRole("tab", { name: "Aperçu" }).click();
  const prev = page.getByRole("region", { name: "Aperçu du contrôle" });
  for (const choice of ["mail", "autre"]) {
    await prev.getByRole("combobox").selectOption(choice);
    await expect(prev.getByRole("status").filter({ hasText: "Adresse e-mail : Ce champ est obligatoire" })).toBeVisible();
    await prev.getByLabel("Adresse").fill(`${choice}@b.fr`);
    await expect(prev.getByLabel("Valeur produite")).toContainText(`"value": "${choice}"`);
    await expect(prev.getByLabel("Valeur produite")).toContainText(`"adresse": "${choice}@b.fr"`);
  }

  // tableau : colonne déroulante par ligne, total sous la colonne numérique
  const slug = `tab-${id}`;
  const spec = {
    version: 1, mode: "repeat", display: "table", totals: ["qte"], row_label: "Article",
    parts: [
      part("produit", "Produit", { type: "select", options: [{ value: "a", label: "Alpha" }, { value: "b", label: "Bravo" }] }),
      part("qte", "Quantité", { type: "number" }),
    ],
  };
  expect((await call(page, "POST", "/control-library/", { slug, name: `Tableau ${id}`, spec })).status).toBe(201);
  await page.goto(`/library/controls/${slug}/preview`);
  const t = page.getByRole("region", { name: "Aperçu du contrôle" });
  await t.getByRole("button", { name: "Ajouter une ligne" }).click();
  await t.getByRole("button", { name: "Ajouter une ligne" }).click();
  const rows = t.getByRole("group", { name: /^Article \d/ });
  await rows.nth(0).getByLabel("Produit").selectOption("a");
  await rows.nth(0).getByLabel("Quantité").fill("2");
  await rows.nth(1).getByLabel("Produit").selectOption("b");
  await rows.nth(1).getByLabel("Quantité").fill("5");
  await expect(t.getByLabel("Totaux")).toContainText("Total Quantité : 7");
  await expect(t.getByLabel("Valeur produite")).toContainText('"produit": "b"');
  await expectNoA11yViolations(page);
});

test("menu déroulant à choix multiples : recherche, groupes, options grisées", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `marques-${id}`;
  const spec = {
    version: 1, mode: "items", display: "dropdown", placeholder: "Entrez ici.",
    items: [
      { value: "saab", label: "Saab" }, { value: "opel", label: "Opel" }, { value: "mercedes", label: "Mercedes", disabled: true },
      { value: "chrysler", label: "Chrysler", group: "US Brands" }, { value: "ford", label: "Ford", group: "US Brands" },
    ],
  };
  expect((await call(page, "POST", "/control-library/", { slug, name: `Marques ${id}`, spec })).status).toBe(201);
  await page.goto(`/library/controls/${slug}/preview`);
  const prev = page.getByRole("region", { name: "Aperçu du contrôle" });
  await prev.getByRole("combobox", { name: /Marques/ }).or(prev.getByPlaceholder("Entrez ici.")).first().click();
  const pop = prev.getByRole("group", { name: "Options" });
  await expect(pop.getByText("US Brands", { exact: true })).toBeVisible();
  await expect(pop.getByRole("checkbox", { name: "Mercedes" })).toBeDisabled();
  await pop.getByRole("checkbox", { name: "Saab" }).check();
  await prev.getByPlaceholder("Entrez ici.").fill("for");
  await expect(pop.getByRole("checkbox")).toHaveCount(1);
  await pop.getByRole("checkbox", { name: "Ford" }).check();
  await expect(prev.getByLabel("Valeur produite")).toContainText('"saab"');
  await expect(prev.getByLabel("Valeur produite")).toContainText('"ford"');
  await expect(prev.getByRole("list", { name: "Sélection" })).toContainText("Saab");
  await page.keyboard.press("Escape");
  await expect(pop).toHaveCount(0);
  await expectNoA11yViolations(page);
});

test("designer d'options : groupes parent → enfants, ajout en masse, options grisées", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  await page.goto("/library/controls");
  await page.getByRole("button", { name: "Nouveau contrôle" }).click();
  const dlg = page.getByRole("dialog", { name: "Nouveau contrôle" });
  await dlg.getByLabel("Nom du contrôle").fill(`Voitures ${id}`);
  await dlg.getByRole("radio", { name: /Liste de choix/ }).click();
  await dlg.getByRole("checkbox", { name: "Plusieurs choix possibles" }).check();
  await dlg.getByRole("button", { name: "Créer" }).click();
  await section(page, "Options");
  const od = page.locator(".cd-main");
  await od.getByRole("radio", { name: /Menu avec recherche/ }).click();
  await od.getByText("Ajouter plusieurs options d'un coup").click();
  await od.getByLabel(/Une option par ligne/).fill("Saab\nMercedes\n# US Brands\nFord\nPlymouth");
  await od.getByRole("button", { name: "Ajouter la liste" }).click();
  const tree = od.getByRole("navigation", { name: "Structure des options" });
  await expect(tree.getByRole("button", { name: /US Brands/ })).toBeVisible();
  // Mercedes grisée, Plymouth déplacée hors du groupe puis remise dedans
  await tree.getByRole("button", { name: /^Mercedes/ }).click();
  await od.getByLabel("Désactivée", { exact: false }).check();
  await tree.getByRole("button", { name: /^Plymouth/ }).click();
  await od.getByRole("button", { name: "Sortir du groupe" }).click();
  await expect(od.getByLabel("Groupe", { exact: true })).toHaveValue("");
  await od.getByRole("button", { name: "Placer dans le groupe au-dessus" }).click();
  await expect(od.getByLabel("Groupe", { exact: true })).not.toHaveValue("");
  await expectNoA11yViolations(page);
  await expect(page.getByRole("navigation", { name: "Sections du contrôle" }).getByRole("button", { name: /Options/ })).toContainText("5");
  await page.getByRole("tablist", { name: "Contrôle" }).getByRole("tab", { name: "Aperçu" }).click();
  const prev = page.getByRole("region", { name: "Aperçu du contrôle" });
  await prev.getByRole("combobox").click();
  await expect(prev.getByRole("group", { name: "Options" }).getByText("US Brands", { exact: true })).toBeVisible();
  await expect(prev.getByRole("checkbox", { name: "Mercedes" })).toBeDisabled();
});

test("contrôle à options fournies par le formulaire : essayé avec des options d'exemple, puis la lecture du service est celle du champ du formulaire", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  await page.goto("/library/controls");
  await page.getByRole("button", { name: "Nouveau contrôle" }).click();
  const dlg = page.getByRole("dialog", { name: "Nouveau contrôle" });
  await dlg.getByLabel("Nom du contrôle").fill(`Marques ${id}`);
  await dlg.getByRole("radio", { name: /Liste de choix/ }).click();
  await dlg.getByRole("checkbox", { name: "Plusieurs choix possibles" }).check();
  await dlg.getByRole("button", { name: "Créer" }).click();
  await section(page, "Options");
  const od = page.locator(".cd-main");
  await od.getByRole("radio", { name: /Menu avec recherche/ }).click();
  // le contrôle ne porte aucun appel : il déclare seulement que ses options viennent du formulaire
  await expect(page.getByText("Données depuis un service")).toHaveCount(0);
  await od.getByRole("checkbox", { name: /Options fournies par le formulaire/ }).check();
  await expectNoA11yViolations(page);
  await page.getByRole("tablist", { name: "Contrôle" }).getByRole("tab", { name: "Aperçu" }).click();
  const prev = page.getByRole("region", { name: "Aperçu du contrôle" });
  const options = [{ value: "saab", label: "Saab" }, { value: "ford", label: "Ford", group: "US Brands" }, { value: "plymouth", label: "Plymouth", group: "US Brands", disabled: true }];
  await prev.getByLabel(/Options d'exemple fournies par un formulaire/).fill(JSON.stringify(options));
  await prev.getByRole("combobox").click();
  const pop = prev.getByRole("group", { name: "Options" });
  await expect(pop.getByText("US Brands", { exact: true })).toBeVisible();
  await expect(pop.getByRole("checkbox", { name: "Plymouth" })).toBeDisabled();
  await pop.getByRole("checkbox", { name: "Ford" }).check();
  await expect(prev.getByLabel("Valeur produite")).toContainText('"ford"');
  // publication, puis un formulaire utilise le contrôle et lit SON service
  await page.getByRole("button", { name: "Publier" }).click();
  await page.getByRole("dialog", { name: "Publier une version" }).getByRole("button", { name: "Publier" }).click();
  await expect(page.getByText("v1.0.0 publiée")).toBeVisible();
  const slug = `svc-${id}`;
  expect((await call(page, "POST", "/form-library/", { slug, name: `Formulaire ${id}` })).status).toBe(201);
  await page.goto(`/library/forms/${slug}`);
  await page.waitForLoadState("networkidle"); // le formulaire est chargé : il n'écrasera pas les ajouts qui suivent
  const pal = page.getByRole("complementary", { name: "Palette de champs" });
  const grid = page.getByRole("group", { name: /Grille de la section/ });
  await pal.getByRole("button", { name: `Marques ${id}` }).click();
  await expect(grid.getByRole("button", { name: new RegExp(`Marques ${id}, ligne 1`) })).toBeVisible();
  await pal.getByRole("button", { name: "Texte court" }).click(); // un autre champ à lire dans les conditions
  await expect(grid.getByRole("button", { name: /Texte court, ligne 2/ })).toBeVisible();
  await grid.getByRole("button", { name: new RegExp(`Marques ${id}, ligne 1`) }).click();
  // la lecture du service s'édite dans une fenêtre centrée, comme les conditions : rien n'est appliqué avant « Appliquer »
  const props = page.getByRole("complementary", { name: "Propriétés" });
  await expect(props.getByText("Aucun service")).toBeVisible();
  await props.getByRole("button", { name: "Lire les données d'un service" }).click();
  const svc = page.getByRole("dialog", { name: /Service — / });
  await svc.getByLabel(/Options lues depuis un service/).check();
  await svc.locator("#fp-options-connection").fill("svc");
  await svc.locator("#fp-options-path").fill("/marques?q={recherche}");
  await svc.locator("#fp-options-items_path").fill("data.rows");
  await svc.locator("#fp-options-value_path").fill("id");
  await svc.locator("#fp-options-label_path").fill("name");
  await svc.locator("#fp-options-group_path").fill("region");
  await svc.locator("#fp-options-disabled_path").fill("off");
  // les zones de saisie d'une même rangée sont alignées, même si un libellé passe sur deux lignes
  const top = async (id: string) => (await svc.locator(id).boundingBox())!.y;
  expect(Math.abs((await top("#fp-options-group_path")) - (await top("#fp-options-disabled_path")))).toBeLessThan(1);
  expect(Math.abs((await top("#fp-options-items_path")) - (await top("#fp-options-value_path")))).toBeLessThan(1);
  const reply = { data: { rows: [{ id: "saab", name: "Saab" }, { id: "ford", name: "Ford", region: "US Brands" }] }, selection: ["ford"] };
  await svc.locator("#try-options").fill(JSON.stringify(reply));
  await svc.getByRole("button", { name: "Lire cette réponse" }).click();
  await expect(svc.getByRole("list", { name: "Options lues" })).toContainText("US Brands › Ford");
  await svc.locator("#try-options").fill('{"autre": 1}');
  await svc.getByRole("button", { name: "Lire cette réponse" }).click();
  await expect(svc.getByText(/ne mène pas à une liste/)).toBeVisible();
  await svc.getByRole("tab", { name: /Valeur préremplie/ }).click();
  await svc.getByLabel(/Valeur préremplie par un service/).check();
  await svc.locator("#fp-prefill-connection").fill("svc");
  await svc.locator("#fp-prefill-path").fill("/selection");
  await svc.locator("#fp-prefill-value_path").fill("selection");
  await svc.locator("#try-prefill").fill(JSON.stringify(reply));
  await svc.getByRole("button", { name: "Lire cette réponse" }).click();
  await expect(svc.getByLabel("Valeur lue")).toContainText('"ford"');
  await expectNoA11yViolations(page);
  // annuler ne change rien ; appliquer résume la lecture dans les propriétés
  await svc.getByRole("button", { name: "Annuler" }).click();
  await expect(props.getByText("Aucun service")).toBeVisible();
  await props.getByRole("button", { name: "Lire les données d'un service" }).click();
  await svc.getByLabel(/Options lues depuis un service/).check();
  await svc.locator("#fp-options-connection").fill("svc");
  await svc.locator("#fp-options-path").fill("/marques");
  await svc.getByRole("button", { name: "Appliquer" }).click();
  await expect(props.locator(".fb-rsum").filter({ hasText: "Options :" })).toContainText("/marques");
  await expect(props.getByRole("button", { name: "Gérer les données d'un service" })).toBeVisible();
  // le chargement est automatique, ou soumis à « Conditions et règles » : aucun bouton de rechargement, un effet « Charger les données »
  await props.getByRole("button", { name: /Ajouter des conditions/ }).click();
  const rules = page.getByRole("dialog", { name: /Conditions du champ/ });
  await rules.getByRole("button", { name: "Charger les données du service si…" }).click();
  await expect(rules.getByRole("status", { name: "Résumé de la règle" })).toContainText("charger les options depuis le service");
  await rules.getByRole("button", { name: "Appliquer" }).click();
  await expect(props.getByText(/charger les options depuis le service|Quand/).first()).toBeVisible();
});

test("éditeur de tableau : colonnes, liste déroulante par cellule, total, formule, aperçu", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const name = `Commande ${id}`;
  await page.goto("/library/controls");
  await page.getByRole("button", { name: "Nouveau contrôle" }).click();
  const dlg = page.getByRole("dialog", { name: "Nouveau contrôle" });
  await dlg.getByLabel("Nom du contrôle").fill(name);
  await dlg.getByRole("radio", { name: /Lignes répétées/ }).click();
  await dlg.getByRole("button", { name: "Créer" }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  // le type est choisi à la création : il n'y a plus de section pour le changer
  await expect(page.getByRole("navigation", { name: "Sections du contrôle" }).getByRole("button", { name: "Type" })).toHaveCount(0);

  await section(page, "Lignes");
  const main = page.locator(".cd-main");
  await main.getByRole("radio", { name: /Tableau/ }).click();
  await section(page, "Colonnes");
  const cols = main.getByRole("toolbar", { name: "Colonnes du tableau" });

  await cols.getByRole("button", { name: "Colonne", exact: true }).click();
  await main.getByLabel("Titre de la colonne").fill("Produit");
  await main.getByLabel("Contrôle de la cellule").selectOption("select");
  await main.getByRole("textbox", { name: "Choix 1" }).fill("Alpha");
  await main.getByRole("button", { name: "Ajouter un choix" }).click();
  await main.getByRole("textbox", { name: "Choix 2" }).fill("Bravo");

  await cols.getByRole("button", { name: "Colonne", exact: true }).click();
  await main.getByLabel("Titre de la colonne").fill("Quantité");
  await main.getByLabel("Contrôle de la cellule").selectOption("number");
  await main.getByRole("checkbox", { name: "Total sous la colonne" }).check();
  await expect(cols.getByRole("button", { name: /Quantité/ })).toContainText("Σ");

  await cols.getByRole("button", { name: "Colonne", exact: true }).click();
  await main.getByLabel("Titre de la colonne").fill("Prix");
  await main.getByLabel("Contrôle de la cellule").selectOption("number");
  await main.getByRole("checkbox", { name: "Obligatoire" }).check();
  await expect(main.getByRole("region", { name: "Aperçu du tableau" }).getByRole("group", { name: /^Ligne \d/ })).toHaveCount(2);
  await expectNoA11yViolations(page);

  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 }); // enregistrement automatique
  const saved = await call(page, "GET", `/control-library/${name.toLowerCase().replace(/ /g, "-")}/`);
  expect(saved.data.spec.display).toBe("table");
  expect(saved.data.spec.parts.map((p: { label: string; type: string }) => `${p.label}:${p.type}`)).toEqual(["Produit:select", "Quantité:number", "Prix:number"]);
  expect(saved.data.spec.parts[2].required).toBe(true);
  expect(saved.data.spec.totals).toHaveLength(1);

  // retour à l'éditeur complet puis aux colonnes
  await main.getByRole("button", { name: "Éditeur complet des champs" }).click();
  await expect(main.getByRole("button", { name: "Retour à l'éditeur de colonnes" })).toBeVisible();
});

test("onglet Test : options et valeur envoyées en JSON, enfants remplis, refus d'une valeur non conforme", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `essai-${id}`;
  const spec = { version: 1, mode: "items", dynamic: true, items: [{ value: "x", label: "X" }], parts: [part("couleur", "Couleur")] };
  expect((await call(page, "POST", "/control-library/", { slug, name: `Essai ${id}`, spec })).status).toBe(201);
  await page.goto(`/library/controls/${slug}/test`);
  const t = page.getByRole("region", { name: "Test du contrôle" });

  await t.getByLabel("Options (JSON)").fill(JSON.stringify([{ value: "ford", label: "Ford", group: "US Brands" }, { value: "ford", label: "Doublon" }, { label: "Sans valeur" }, { value: "saab", label: "Saab" }]));
  await t.getByRole("button", { name: "Envoyer les options" }).click();
  await expect(t.getByText("2 option(s) retenue(s), 2 ignorée(s)")).toBeVisible();

  await t.getByLabel("Valeur (JSON)").fill(JSON.stringify([{ value: "ford", couleur: "rouge" }]));
  await t.getByRole("button", { name: "Envoyer la valeur" }).click();
  await expect(t.getByText("Valeur acceptée : le contrôle est rempli")).toBeVisible();
  await expect(t.getByLabel("Valeur produite par le test")).toContainText('"couleur": "rouge"');

  await t.getByLabel("Valeur (JSON)").fill(JSON.stringify({ value: "ford" }));
  await t.getByRole("button", { name: "Envoyer la valeur" }).click();
  await expect(t.getByRole("alert")).toContainText("liste");
  await t.getByLabel("Options (JSON)").fill("pas du json");
  await t.getByRole("button", { name: "Envoyer les options" }).click();
  await expect(t.getByRole("alert")).toContainText("JSON invalide");
  await t.getByRole("button", { name: "Exemple" }).last().click();
  await expect(t.getByLabel("Valeur (JSON)")).toHaveValue(/ford/);
  await expectNoA11yViolations(page);
});

test("onglet Test : l'option choisie pré-remplit ses champs enfants (nom, prénom) depuis le JSON envoyé", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `perso-${id}`;
  const spec = { version: 1, mode: "choice", dynamic: true, items: [{ value: "x", label: "X" }], parts: [part("nom", "Nom"), part("prenom", "Prénom")] };
  expect((await call(page, "POST", "/control-library/", { slug, name: `Personne ${id}`, spec })).status).toBe(201);
  await page.goto(`/library/controls/${slug}/test`);
  const t = page.getByRole("region", { name: "Test du contrôle" });
  await t.getByLabel("Options (JSON)").fill(JSON.stringify([
    { value: "option-1", nom: "Exemple", prenom: "Exemple" },
    { value: "option-2", nom: "Exemple2", prenom: "Exemple2" },
  ]));
  await t.getByRole("button", { name: "Envoyer les options" }).click();
  await expect(t.getByText("2 option(s) retenue(s) (dont 2 avec des valeurs pour les champs enfants)")).toBeVisible();

  const choice = t.getByRole("group", { name: "Personne " + id }).getByRole("combobox");
  await choice.selectOption("option-2");
  await expect(t.getByLabel("Nom", { exact: true })).toHaveValue("Exemple2");
  await expect(t.getByLabel("Prénom")).toHaveValue("Exemple2");
  await expect(t.getByLabel("Valeur produite par le test")).toContainText('"prenom": "Exemple2"');
  await choice.selectOption("option-1");
  await expect(t.getByLabel("Nom", { exact: true })).toHaveValue("Exemple");
  await t.getByLabel("Nom", { exact: true }).fill("Modifié");
  await expect(t.getByLabel("Valeur produite par le test")).toContainText('"nom": "Modifié"');
});

test("onglet Test : un contrôle sans champ enfant ni options reçues l'explique et nomme les clés non utilisées", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `vide-${id}`;
  expect((await call(page, "POST", "/control-library/", { slug, name: `Vide ${id}`, spec: { version: 1, mode: "choice", items: [{ value: "option-1", label: "Option 1" }], parts: [] } })).status).toBe(201);
  await page.goto(`/library/controls/${slug}/test`);
  const t = page.getByRole("region", { name: "Test du contrôle" });
  await expect(t.getByText("n'a aucun champ enfant")).toBeVisible();
  await t.getByLabel("Options (JSON)").fill(JSON.stringify([{ value: "option-1", nom: "Exemple", prenom: "Exemple" }]));
  await t.getByRole("button", { name: "Envoyer les options" }).click();
  await expect(t.getByText("sans champ enfant correspondant, donc non utilisée(s) : nom, prenom")).toBeVisible();
});

test("menu déroulant à choix multiples : un clic sur le texte d'une option la coche et ouvre ses champs enfants pré-remplis", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `menu-${id}`;
  const spec = { version: 1, mode: "items", dynamic: true, display: "dropdown", items: [{ value: "x", label: "X" }], parts: [part("nom", "Nom"), part("prenom", "Prénom")] };
  expect((await call(page, "POST", "/control-library/", { slug, name: `Menu ${id}`, spec })).status).toBe(201);
  await page.goto(`/library/controls/${slug}/test`);
  const t = page.getByRole("region", { name: "Test du contrôle" });
  await t.getByLabel("Options (JSON)").fill(JSON.stringify([{ value: "option-1", nom: "Exemple", prenom: "Exemple" }, { value: "option-2", nom: "Exemple2", prenom: "Exemple2" }]));
  await t.getByRole("button", { name: "Envoyer les options" }).click();
  await t.getByRole("combobox").click();
  await t.locator(".opk label").filter({ hasText: "option-2" }).click(); // sur le texte, pas sur la case
  await expect(t.getByLabel("Nom", { exact: true })).toHaveValue("Exemple2");
  await expect(t.getByLabel("Valeur produite par le test")).toContainText('"prenom": "Exemple2"');
  await t.locator(".opk label").filter({ hasText: "option-1" }).click();
  await expect(t.getByText("2 coché(s)")).toBeVisible();
});

test("section Documentation : schéma, exemple, champs et interactions, mis à jour pendant la conception", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `doc-${id}`;
  expect((await call(page, "POST", "/control-library/", { slug, name: `Doc ${id}`, spec: { version: 1, mode: "repeat", parts: [part("produit", "Produit", { required: true }), part("qte", "Quantité", { type: "number" })] } })).status).toBe(201);
  await page.goto(`/library/controls/${slug}/design`);
  await section(page, "Documentation");
  const main = page.locator(".cd-main");
  await expect(main.getByLabel("Schéma JSON de la valeur", { exact: true })).toContainText('"required": [');
  await expect(main.getByLabel("Schéma JSON de la valeur", { exact: true })).toContainText('"produit"');
  await expect(main.getByLabel("Schéma JSON de la valeur", { exact: true })).not.toContainText('"email"');
  await expect(main.getByRole("cell", { name: "{{ inputs.<clé>[0].produit }}" })).toBeVisible();
  await expect(main.getByRole("cell", { name: 'sum_col({<clé>}, "qte")' })).toBeVisible();

  // pendant la conception : un champ ajouté apparaît aussitôt dans la documentation
  await section(page, "Champs");
  await main.getByRole("button", { name: "Adresse e-mail", exact: true }).click();
  await main.getByLabel("Clé du sous-champ").fill("courriel");
  await section(page, "Documentation");
  await expect(main.getByLabel("Schéma JSON de la valeur", { exact: true })).toContainText('"courriel"');
  await expect(main.getByLabel("Schéma JSON de la valeur", { exact: true })).toContainText('"format": "email"');
  await expect(main.getByRole("cell", { name: "courriel" })).toBeVisible();
  await expect(main.getByLabel("Exemple de valeur", { exact: true })).toContainText('"courriel": "nom@exemple.fr"');
  await expectNoA11yViolations(page);
});

test("documentation en volet latéral : visible pendant la conception sur grand écran, masquable, mémorisée", async ({ page }) => {
  await login(page);
  await page.setViewportSize({ width: 1600, height: 900 });
  const id = Date.now().toString().slice(-6);
  const slug = `volet-${id}`;
  expect((await call(page, "POST", "/control-library/", { slug, name: `Volet ${id}`, spec: { version: 1, mode: "group", parts: [part("nom", "Nom")] } })).status).toBe(201);
  await page.goto(`/library/controls/${slug}/design`);
  const side = page.getByRole("complementary", { name: "Documentation en direct" });
  await expect(side).toBeVisible();
  await expect(side.getByLabel("Schéma JSON de la valeur", { exact: true })).toContainText('"nom"');

  // on conçoit dans une autre section : le volet suit
  await section(page, "Champs");
  await page.locator(".cd-main").getByRole("button", { name: "Adresse e-mail", exact: true }).click();
  await page.locator(".cd-main").getByLabel("Clé du sous-champ").fill("courriel");
  await expect(side.getByLabel("Schéma JSON de la valeur", { exact: true })).toContainText('"courriel"');
  await expectNoA11yViolations(page);

  // masqué, puis le choix est retenu au rechargement
  await side.getByRole("button", { name: "Masquer la documentation" }).click();
  await expect(side).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("button", { name: "Afficher la documentation" })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Documentation en direct" })).toHaveCount(0);
  await page.getByRole("button", { name: "Afficher la documentation" }).click();
  await expect(page.getByRole("complementary", { name: "Documentation en direct" })).toBeVisible();

  // sur un écran plus étroit : pas de volet, la section Documentation reste
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.getByRole("complementary", { name: "Documentation en direct" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Afficher la documentation" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Sections du contrôle" }).getByRole("button", { name: "Documentation" })).toBeVisible();
});

test("lignes en tableau : les champs d'une ligne restent côte à côte, en colonnes, même dans une zone étroite", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `colonnes-${id}`;
  const spec = { version: 1, mode: "repeat", display: "table", parts: [part("produit", "Produit"), part("qte", "Quantité", { type: "number" }), part("prix", "Prix", { type: "number" })] };
  expect((await call(page, "POST", "/control-library/", { slug, name: `Colonnes ${id}`, spec })).status).toBe(201);
  for (const width of [1280, 760]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`/library/controls/${slug}/preview`);
    const t = page.getByRole("region", { name: "Aperçu du contrôle" });
    await t.getByRole("button", { name: "Ajouter une ligne" }).click();
    const rowBox = t.getByRole("group", { name: /^Ligne 1/ });
    const [a, b, c] = await Promise.all(["Produit", "Quantité", "Prix"].map((n) => rowBox.getByLabel(n).boundingBox()));
    expect(a && b && c, `largeur ${width}`).toBeTruthy();
    // même rangée (même ordonnée) et ordre de gauche à droite
    expect(Math.abs((a?.y ?? 0) - (b?.y ?? 99))).toBeLessThan(4);
    expect(Math.abs((b?.y ?? 0) - (c?.y ?? 99))).toBeLessThan(4);
    expect((a?.x ?? 0) < (b?.x ?? 0) && (b?.x ?? 0) < (c?.x ?? 0)).toBe(true);
  }
});

test("contrôles : arborescence de dossiers comme les formulaires — créer dans un dossier, filtrer, déplacer", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const dossier = `Adresses${id}`;
  await page.goto("/library/controls");
  const tree = page.getByRole("navigation", { name: "Dossiers de contrôles" });
  await expect(tree).toBeVisible();
  await expectNoA11yViolations(page);
  await page.getByRole("button", { name: "Nouveau contrôle" }).click();
  const dlg = page.getByRole("dialog", { name: "Nouveau contrôle" });
  await dlg.getByLabel("Nom du contrôle").fill(`Rangé ${id}`);
  await dlg.getByRole("button", { name: "Créer" }).click();
  await expect(page.getByRole("heading", { level: 1, name: `Rangé ${id}` })).toBeVisible();
  // le dossier se règle dans « Général » ; l'enregistrement est automatique
  await page.getByRole("navigation", { name: "Sections du contrôle" }).getByRole("button", { name: "Général" }).click();
  await page.getByLabel("Dossier").fill(dossier);
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 });
  await page.goto("/library/controls");
  await page.getByRole("navigation", { name: "Dossiers de contrôles" }).getByRole("button", { name: new RegExp(dossier) }).click();
  await expect(page.getByRole("link", { name: new RegExp(`Rangé ${id}`) }).first()).toBeVisible();
  await page.getByRole("button", { name: `Déplacer Rangé ${id} dans un dossier` }).click();
  await page.getByRole("dialog").getByLabel("Dossier").fill(`${dossier}/Sous`);
  await page.getByRole("dialog").getByRole("button", { name: "Déplacer", exact: true }).click();
  await expect(page.getByText(`${dossier}/Sous`).first()).toBeVisible();
});
