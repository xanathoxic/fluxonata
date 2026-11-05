import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, login, publishViaApi } from "./helpers";

async function call(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ({ method, path, body }) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    const r = await fetch(`/api/v1/workspaces/demo${path}`, { method, headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: body ? JSON.stringify(body) : undefined });
    return { status: r.status, data: await r.json().catch(() => ({})) };
  }, { method, path, body });
}
const text = (key: string, extra: Record<string, unknown> = {}) => ({ key, label: key.toUpperCase(), type: "text", ...extra });
const card = (page: Page, name: string) => page.getByRole("button", { name: new RegExp(`^${name}( \\*)? [^,]*, ligne`) });

test("mise en page : déplacer et redimensionner au clavier, à la souris et par les nombres ; l'aperçu suit", async ({ page }) => {
  await login(page);
  const slug = `lay-${Date.now().toString().slice(-6)}`;
  expect((await call(page, "POST", "/form-library/", { slug, name: `Mise en page ${slug}`, definition: { version: 1, sections: [{ id: "s1", title: "Test", fields: [text("a"), text("b"), text("c")] }] } })).status).toBe(201);
  await page.goto(`/forms/${slug}`);
  await expect(card(page, "A")).toContainText("Texte court");
  await expect(card(page, "A")).toHaveAttribute("aria-label", /ligne 1, colonne 1, largeur 12 sur 12/);
  await expect(card(page, "B")).toHaveAttribute("aria-label", /ligne 2, colonne 1, largeur 12 sur 12/);

  // clavier : A et B partagent la première ligne (Maj + flèches = largeur, flèches = position)
  await card(page, "A").focus();
  for (let i = 0; i < 6; i++) await page.keyboard.press("Shift+ArrowLeft");
  await expect(card(page, "A")).toHaveAttribute("aria-label", /largeur 6 sur 12/);
  await card(page, "B").focus();
  for (let i = 0; i < 6; i++) await page.keyboard.press("Shift+ArrowLeft");
  for (let i = 0; i < 6; i++) await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowUp");
  await expect(card(page, "B")).toHaveAttribute("aria-label", /ligne 1, colonne 7, largeur 6 sur 12/);
  await expect(card(page, "C")).toHaveAttribute("aria-label", /ligne 2, colonne 1/);

  // un champ déposé sur un autre ne l'écrase pas : il prend une ligne à lui
  await card(page, "C").focus();
  for (let i = 0; i < 6; i++) await page.keyboard.press("Shift+ArrowLeft");
  await page.keyboard.press("ArrowUp");
  await expect(card(page, "C")).toHaveAttribute("aria-label", /ligne 1, colonne 1, largeur 6/);
  await expect(card(page, "A")).toHaveAttribute("aria-label", /ligne 2, colonne 1/);
  await expect(card(page, "B")).toHaveAttribute("aria-label", /ligne 2, colonne 7/);

  // souris : tirer le bord droit de A de 6 à 3 colonnes
  const grid = page.locator(".fb-lgrid").first();
  const box = (await grid.boundingBox())!;
  const handle = card(page, "A").locator(".fb-handle");
  const hb = (await handle.boundingBox())!;
  await page.mouse.move(hb.x + hb.width / 2, hb.y + hb.height / 2);
  await page.mouse.down();
  await page.mouse.move(hb.x + hb.width / 2 - (box.width / 12) * 1.5, hb.y + hb.height / 2, { steps: 6 });
  await page.mouse.move(hb.x + hb.width / 2 - (box.width / 12) * 3, hb.y + hb.height / 2, { steps: 6 });
  await page.mouse.up();
  await expect(card(page, "A")).toHaveAttribute("aria-label", /largeur 3 sur 12/);

  // les boutons d'outil : la voie sans glisser
  await card(page, "A").click();
  await page.getByRole("button", { name: "Élargir d'une colonne" }).click();
  await expect(card(page, "A")).toHaveAttribute("aria-label", /largeur 4 sur 12/);
  await page.getByRole("button", { name: "Élargir d'une colonne" }).click();
  await page.getByRole("button", { name: "Élargir d'une colonne" }).click();
  await expect(card(page, "A")).toHaveAttribute("aria-label", /largeur 6 sur 12/);
  // plus de panneau de placement numérique : le glisser, les poignées, le clavier et ces boutons suffisent
  await expect(page.getByLabel("Largeur (colonnes)")).toHaveCount(0);
  await expect(page.getByText("Placement sur la grille")).toHaveCount(0);
  await expectNoA11yViolations(page);

  // l'aperçu applique le placement : C, puis A et B côte à côte
  await page.getByRole("tab", { name: "Aperçu" }).click();
  const a = (await page.getByLabel("A", { exact: true }).boundingBox())!;
  const b = (await page.getByLabel("B", { exact: true }).boundingBox())!;
  const c = (await page.getByLabel("C", { exact: true }).boundingBox())!;
  expect(Math.abs(a.y - b.y)).toBeLessThan(4);
  expect(b.x).toBeGreaterThan(a.x + a.width - 4);
  expect(a.y).toBeGreaterThan(c.y);
});

test("blocs de contenu : titre, texte et séparateur s'ajoutent, se placent et ne sont pas des entrées", async ({ page }) => {
  await login(page);
  const slug = `blk-${Date.now().toString().slice(-6)}`;
  expect((await call(page, "POST", "/form-library/", { slug, name: `Blocs ${slug}`, definition: { version: 1, sections: [{ id: "s1", title: "Test", fields: [text("a")] }] } })).status).toBe(201);
  await page.goto(`/forms/${slug}`);
  await page.getByRole("button", { name: "Titre", exact: true }).click();
  await page.getByLabel("Texte du titre").fill("Votre demande");
  await page.getByRole("button", { name: "Texte", exact: true }).click();
  await page.getByLabel("Texte", { exact: true }).fill("Remplissez les champs ci-dessous.");
  await page.getByRole("button", { name: "Séparateur", exact: true }).click();
  await expectNoA11yViolations(page);
  await page.getByRole("tab", { name: "Aperçu" }).click();
  await expect(page.getByRole("heading", { name: "Votre demande" })).toBeVisible();
  await expect(page.getByText("Remplissez les champs ci-dessous.")).toBeVisible();
  await expect(page.locator(".fb-divider")).toHaveCount(1);
  await expect(page.getByLabel("A", { exact: true })).toBeVisible();
  // un bloc n'est pas un champ à remplir
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 }); // enregistrement automatique
});

test("style : couleur, libellés à gauche, largeur et bouton ; une couleur illisible est refusée ; la page publique l'applique", async ({ page, browser }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `sty-${id}`;
  const wf = `wf-${slug}`;
  expect(await publishViaApi(page, wf, { inputs: { type: "object", properties: { a: { type: "string" }, b: { type: "string" } }, required: [] }, outputs: {}, edges: [], nodes: [{ id: "n", type: "wait", name: "Pause", seconds: 1 }] })).toBe(201);
  expect((await call(page, "POST", "/form-library/", { slug, name: `Style ${slug}`, definition: { version: 1, sections: [{ id: "s1", title: "Test", fields: [text("a", { layout: { row: 1, col: 1, span: 6 } }), text("b", { layout: { row: 1, col: 7, span: 6 } })] }] } })).status).toBe(201);
  await page.goto(`/forms/${slug}/design`);

  await page.getByRole("tab", { name: "Apparence" }).click();
  // la barre de style : de petites icônes, chacune ouvre un menu contextuel d'options en icônes (nom et aide en infobulle)
  const bar = page.getByRole("toolbar", { name: "Style du formulaire" });
  await expect(bar.getByRole("button")).toHaveCount(8);
  await expect(bar.getByRole("button", { name: /^Libellés/ })).toHaveAttribute("title", /Au-dessus/);
  await bar.getByRole("button", { name: /^Libellés/ }).click();
  await expect(page.getByRole("menuitemradio", { name: "Au-dessus" })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByRole("menuitemradio", { name: "À gauche" })).toHaveAttribute("title", "À gauche");
  await expect(page.getByRole("menuitemradio", { name: "À gauche" })).toHaveText(""); // aucune légende visible : des icônes seules
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Escape"); // Échap ferme sans rien changer et rend le focus au bouton
  await expect(page.getByRole("menu")).toHaveCount(0);
  await expect(bar.getByRole("button", { name: /^Libellés/ })).toBeFocused();
  await bar.getByRole("button", { name: /^Libellés/ }).click();
  await page.getByRole("menuitemradio", { name: "À gauche" }).click();
  await expect(page.getByRole("menu")).toHaveCount(0); // un choix referme le menu
  await expect(bar.getByRole("button", { name: /^Libellés/ })).toHaveAttribute("title", /À gauche/); // le bouton montre le choix courant
  await bar.getByRole("button", { name: /^Bouton d'envoi/ }).click();
  await page.getByRole("menuitemradio", { name: "Pleine largeur du bouton" }).click();
  await bar.getByRole("button", { name: /^Largeur du formulaire/ }).click();
  await page.getByRole("menuitemradio", { name: "Large", exact: true }).click();
  await bar.getByRole("button", { name: /^Couleur d'accent/ }).click();
  await page.getByRole("button", { name: "Couleur #1d4ed8" }).click();
  await page.getByLabel("Code couleur").fill("#ffff00");
  await expect(page.getByRole("alert").filter({ hasText: "trop claire" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Couleur #1d4ed8" })).toHaveAttribute("aria-pressed", "true"); // le refus ne change rien
  await page.keyboard.press("Escape");
  const preview = page.locator(".fb-form").last();
  await expect(preview).toHaveClass(/fb-l-left/);
  await expect(preview).toHaveClass(/fb-b-full/);
  expect(await preview.evaluate((el) => getComputedStyle(el).getPropertyValue("--accent").trim())).toBe("#1d4ed8");
  expect((await preview.boundingBox())!.width).toBeLessThanOrEqual(961);
  await expectNoA11yViolations(page);

  // enregistrer, publier, rattacher à un formulaire public : la page publique porte le style et le placement
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 }); // enregistrement automatique
  await page.getByRole("button", { name: "Publier", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Publier" }).click();
  await expect(page.getByText("v1.0.0 publiée")).toBeVisible();
  const pf = await call(page, "POST", "/forms/", { name: `Pub ${slug}`, title: `Titre ${slug}`, workflow: wf, environment: "test" });
  expect(pf.status).toBe(201);
  expect((await call(page, "POST", `/forms/${pf.data.id}/attach/`, { form: slug })).status).toBe(200);

  const anon = await browser.newContext();
  const pub = await anon.newPage();
  await pub.goto(pf.data.url);
  const a = (await pub.getByLabel("A", { exact: true }).boundingBox())!;
  const b = (await pub.getByLabel("B", { exact: true }).boundingBox())!;
  expect(b.x).toBeGreaterThan(a.x);
  await expect(pub.locator(".fb-form")).toHaveClass(/fb-l-left/);
  await expectNoA11yViolations(pub);
  await expect(pub.getByRole("button", { name: "Envoyer" })).toBeVisible();
  await anon.close();
});

test("barre d'outils : annuler / rétablir, recherche dans la palette, pastille de validité", async ({ page }) => {
  await login(page);
  const slug = `bar-${Date.now().toString().slice(-6)}`;
  expect((await call(page, "POST", "/form-library/", { slug, name: `Barre ${slug}`, definition: { version: 1, sections: [{ id: "s1", title: "Test", fields: [text("a")] }] } })).status).toBe(201);
  await page.goto(`/forms/${slug}`);
  await expect(page.getByRole("status").filter({ hasText: "Valide" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Annuler", exact: true })).toBeDisabled();

  await page.getByLabel("Rechercher un élément").fill("date");
  await expect(page.getByRole("button", { name: "Date", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Nombre", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Date", exact: true }).click();
  await expect(page.getByRole("button", { name: /^Date \*?\s?Date, ligne 2/ })).toBeVisible();
  await page.getByRole("button", { name: "Annuler", exact: true }).click();
  await expect(page.getByRole("button", { name: /^Date \*?\s?Date, ligne 2/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Rétablir" }).click();
  await expect(page.getByRole("button", { name: /^Date \*?\s?Date, ligne 2/ })).toBeVisible();

  // « Ajouter » masque et réaffiche la palette, comme dans l'éditeur de workflows
  await page.getByRole("button", { name: "Ajouter", exact: true }).click();
  await expect(page.getByLabel("Rechercher un élément")).toHaveCount(0);
  await page.getByRole("button", { name: "Ajouter", exact: true }).click();
  await expect(page.getByLabel("Rechercher un élément")).toBeVisible();
  await expectNoA11yViolations(page);
});

test("CSS personnalisé : appliqué à l'aperçu et à la page publique, normalisé, et rien de dangereux n'est accepté", async ({ page, browser }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `css-${id}`;
  const wf = `wf-${slug}`;
  expect(await publishViaApi(page, wf, { inputs: { type: "object", properties: { a: { type: "string" } }, required: [] }, outputs: {}, edges: [], nodes: [{ id: "n", type: "wait", name: "Pause", seconds: 1 }] })).toBe(201);
  const def = { version: 1, sections: [{ id: "s1", title: "Test", fields: [{ key: "t", type: "heading", label: "Mon titre", level: 2 }, text("a")] }] };
  expect((await call(page, "POST", "/form-library/", { slug, name: `Css ${slug}`, definition: def })).status).toBe(201);
  await page.goto(`/forms/${slug}`);
  await page.getByRole("tab", { name: "Apparence" }).click();
  await page.getByRole("tab", { name: "Avancé (CSS)" }).click();
  await page.getByRole("tab", { name: "Code" }).click(); // le mode visuel est celui par défaut ; ici, le code
  const area = page.getByRole("textbox", { name: "CSS personnalisé" });
  await area.fill('.fb-heading{color:#b91c1c}/* commentaire */\n[data-key="a"] input{border-color:#15803d}');
  await expect(page.getByRole("status").filter({ hasText: "CSS valide et appliqué" })).toBeVisible();
  const heading = page.locator(".fb-form").last().locator(".fb-heading");
  await expect(heading).toHaveCSS("color", "rgb(185, 28, 28)");
  await expect(page.locator(".fb-form").last().locator('[data-key="a"] input')).toHaveCSS("border-top-color", "rgb(21, 128, 61)");
  await expectNoA11yViolations(page);

  // du CSS dangereux est refusé avec le numéro de ligne ; le dernier CSS valide reste appliqué
  for (const [bad, expected] of [
    ['.x{background:url(https://evil.example/a.png)}', /Ligne 1.*url/],
    ["body{background:red}", /Élément « body »/],
    [".x{position:fixed;top:0}", /position/],
    ["@import url(x.css);", /@import|non autorisée/],
    [".x{color:red !important}", /important/],
  ] as const) {
    await area.fill(bad);
    await expect(page.getByRole("alert").filter({ hasText: expected })).toBeVisible();
    await expect(heading).toHaveCSS("color", "rgb(185, 28, 28)");
  }
  await area.fill('.fb-heading{color:#b91c1c}\n[data-key="a"] input{border-color:#15803d}');
  await expect(page.getByRole("status").filter({ hasText: "CSS valide et appliqué" })).toBeVisible();

  // enregistré sous la forme normalisée, publié, rattaché : la page publique porte le CSS
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 }); // enregistrement automatique
  const saved = await call(page, "GET", `/form-library/${slug}/`);
  expect(saved.data.definition.css).toBe("& .fb-heading {\n  color: #b91c1c;\n}\n\n& [data-key=\"a\"] input {\n  border-color: #15803d;\n}");
  await page.getByRole("button", { name: "Publier", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Publier" }).click();
  await expect(page.getByText("v1.0.0 publiée")).toBeVisible();
  const pf = await call(page, "POST", "/forms/", { name: `Pub ${slug}`, title: `Titre ${slug}`, workflow: wf, environment: "test" });
  expect((await call(page, "POST", `/forms/${pf.data.id}/attach/`, { form: slug })).status).toBe(200);
  const anon = await browser.newContext();
  const pub = await anon.newPage();
  await pub.goto(pf.data.url);
  await expect(pub.locator(".fb-heading")).toHaveCSS("color", "rgb(185, 28, 28)");
  const own = await pub.locator(".fb-form style").allTextContents(); // the form\'s own stylesheet, nothing else
  expect(own).toHaveLength(1);
  expect(own[0]).toContain("& .fb-heading");
  expect(own[0]).not.toContain("url(");
  await expectNoA11yViolations(pub);
  await anon.close();
});

test("défilement : seul le formulaire défile, la barre d'outils, la palette, les propriétés et la barre de style restent accessibles", async ({ page }) => {
  await login(page);
  const slug = `scr-${Date.now().toString().slice(-6)}`;
  const many = Array.from({ length: 24 }, (_, i) => text(`champ${i + 1}`));
  expect((await call(page, "POST", "/form-library/", { slug, name: `Défilement ${slug}`, definition: { version: 1, sections: [{ id: "s1", title: "Long", fields: many }] } })).status).toBe(201);
  await page.goto(`/forms/${slug}`);
  await expect(card(page, "CHAMP1")).toBeVisible();
  await card(page, "CHAMP1").click();

  const tool = page.getByRole("toolbar", { name: "Outils de l'éditeur de formulaire" });
  const palette = page.getByLabel("Rechercher un élément");
  const props = page.getByRole("complementary", { name: "Propriétés" }).getByRole("heading", { name: "Propriétés" });
  const y = async () => [(await tool.boundingBox())!.y, (await palette.boundingBox())!.y, (await props.boundingBox())!.y];
  const before = await y();
  const canvas = page.locator(".fb-canvas");
  const cb = (await canvas.boundingBox())!;
  await page.mouse.move(cb.x + cb.width / 2, cb.y + 200);
  await page.mouse.wheel(0, 4000);
  await expect.poll(() => canvas.evaluate((el) => el.scrollTop)).toBeGreaterThan(300);
  expect(await y()).toEqual(before); // les menus n'ont pas bougé
  expect(await page.evaluate(() => window.scrollY)).toBe(0); // la page non plus
  await expect(card(page, "CHAMP24")).toBeInViewport();
  await expect(tool).toBeInViewport();
  await expect(palette).toBeInViewport();
  // le menu de placement du champ choisi reste sous la main pendant que le formulaire défile
  await expect(page.getByRole("toolbar", { name: /^Placer/ })).toBeInViewport();
  // la palette fonctionne depuis le bas du formulaire
  await page.getByRole("button", { name: "Date", exact: true }).click();
  await expect(card(page, "Date")).toBeVisible();

  // idem dans l'onglet Style : la barre d'icônes reste, l'aperçu défile
  await page.getByRole("tab", { name: "Apparence" }).click();
  const bar = page.getByRole("toolbar", { name: "Style du formulaire" });
  const by = (await bar.boundingBox())!.y;
  const scroller = page.locator(".fb-stylepanel > .fb-scroll");
  const sb = (await scroller.boundingBox())!;
  await page.mouse.move(sb.x + sb.width / 2, sb.y + 100);
  await page.mouse.wheel(0, 1500);
  await expect.poll(() => scroller.evaluate((el) => el.scrollTop)).toBeGreaterThan(300);
  expect((await bar.boundingBox())!.y).toBe(by);
  await expect(bar).toBeInViewport();
  await bar.getByRole("button", { name: /^Densité/ }).click();
  await expect(page.getByRole("menu", { name: "Densité" })).toBeInViewport();
  await page.keyboard.press("Escape");
  await expect(tool).toBeInViewport();
});

test("CSS sans code : cliquer dans l'aperçu, régler visuellement, CSS généré en direct, états, écrans, thèmes, retour au code", async ({ page, browser }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `nocode-${id}`;
  const wf = `wf-${slug}`;
  expect(await publishViaApi(page, wf, { inputs: { type: "object", properties: { nom: { type: "string" }, mail: { type: "string" } }, required: [] }, outputs: {}, edges: [], nodes: [{ id: "n", type: "wait", name: "Pause", seconds: 1 }] })).toBe(201);
  const def = { version: 1, sections: [{ id: "s1", title: "Test", fields: [{ key: "t", type: "heading", label: "Mon titre", level: 2 }, text("nom", { required: true }), text("mail", { type: "email" })] }] };
  expect((await call(page, "POST", "/form-library/", { slug, name: `NoCode ${slug}`, definition: def })).status).toBe(201);
  await page.goto(`/forms/${slug}`);
  await page.getByRole("tab", { name: "Apparence" }).click();
  await page.getByRole("tab", { name: "Avancé (CSS)" }).click();
  await expect(page.getByRole("tab", { name: "Visuel" })).toHaveAttribute("aria-selected", "true");

  const preview = page.getByRole("region", { name: "Aperçu du formulaire" });
  const code = page.getByLabel("CSS généré");
  await expect(code).toContainText("Le CSS apparaît ici");

  // 1. un clic dans l'aperçu choisit l'élément : le titre du bloc
  await preview.locator(".fb-heading").click();
  await expect(page.getByRole("button", { name: /^Élément à styler : Blocs titre/ })).toBeVisible();
  // 2. des réglages visuels, sans écrire une ligne
  await page.getByRole("tab", { name: "Couleurs et fond" }).click();
  await page.getByLabel("Couleur du texte : code").fill("#b91c1c");
  await expect(preview.locator(".fb-heading")).toHaveCSS("color", "rgb(185, 28, 28)");
  await page.getByRole("tab", { name: "Typographie" }).click();
  await page.getByLabel("Graisse").selectOption("800");
  await page.getByRole("radio", { name: "MAJUSCULES" }).click();
  await expect(preview.locator(".fb-heading")).toHaveCSS("text-transform", "uppercase");
  // le CSS est écrit en direct, sous la forme exacte qui sera enregistrée
  await expect(code).toContainText("& .fb-heading {");
  await expect(code).toContainText("color: #b91c1c;");
  await expect(code).toContainText("font-weight: 800;");
  await expect(page.getByRole("status").filter({ hasText: "CSS appliqué" })).toBeVisible();

  // 3. un autre élément : les libellés, avec un état (survol) montré dans l'aperçu
  await preview.locator("label").first().click();
  await expect(page.getByRole("button", { name: /^Élément à styler : Libellés/ })).toBeVisible();
  await page.getByRole("button", { name: /^État/ }).click();
  await page.getByRole("menuitemradio", { name: "Survol" }).click();
  await page.getByRole("tab", { name: "Couleurs et fond" }).click();
  await page.getByLabel("Couleur du texte : code").fill("#15803d");
  await expect(code).toContainText("& .fb-field > label:hover");
  await expect(preview.locator(".fb-field > label").first()).toHaveCSS("color", "rgb(21, 128, 61)"); // montré sans survoler

  // 4. un champ précis, sur mobile seulement
  await page.getByRole("button", { name: /^État/ }).click();
  await page.getByRole("menuitemradio", { name: "Normal" }).click();
  await page.getByRole("button", { name: /^Portée/ }).click();
  await page.getByRole("menuitemradio", { name: "MAIL", exact: true }).click();
  await page.getByRole("button", { name: /^Écran/ }).click();
  await page.getByRole("menuitemradio", { name: /^Mobile/ }).click();
  await page.getByRole("tab", { name: "Espacement" }).click();
  await page.getByRole("spinbutton", { name: "Marge intérieure", exact: true }).fill("20");
  await expect(code).toContainText("@media (max-width: 600px) {");
  await expect(code).toContainText('[data-key="mail"] .fb-field > label');

  // 5. le menu des règles les liste et permet de les supprimer
  await page.getByRole("button", { name: /^Règles du formulaire/ }).click();
  await expect(page.getByRole("menu", { name: "Règles" }).getByRole("menuitemradio")).toHaveCount(3);
  await page.getByRole("menuitem", { name: /^Supprimer la règle : .*champ mail/ }).click();
  await expect(code).not.toContainText("@media (max-width: 600px)");
  await page.keyboard.press("Escape");
  await expectNoA11yViolations(page);

  // 6. un thème prêt à l'emploi, ajouté aux règles existantes
  await page.getByRole("button", { name: "Thèmes prêts à l'emploi" }).click();
  await page.getByRole("button", { name: "Thème Doux : ajouter aux règles" }).click();
  await expect(code).toContainText("border-radius: 14px;");
  await expect(code).toContainText("color: #b91c1c;"); // les réglages d'avant sont conservés
  await expect(preview.locator(".fb-section")).toHaveCSS("border-top-left-radius", "20px");

  // 7. le même texte en mode code, qu'on peut adapter à la main ; le retour au visuel le relit
  await page.getByRole("button", { name: "Modifier à la main" }).click();
  const area = page.getByRole("textbox", { name: "CSS personnalisé" });
  await expect(area).toHaveValue(/& \.fb-heading \{/);
  await area.fill((await area.inputValue()) + "\n\n& .fb-actions button {\n  letter-spacing: 0.1em;\n}");
  await expect(page.getByRole("status").filter({ hasText: "CSS valide et appliqué" })).toBeVisible();
  await page.getByRole("tab", { name: "Visuel" }).click();
  await page.getByRole("button", { name: /^Règles du formulaire/ }).click();
  await expect(page.getByRole("menu", { name: "Règles" })).toContainText("Boutons");
  await page.keyboard.press("Escape");

  // 8. enregistré, publié, rattaché : la page publique porte ce style
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 }); // enregistrement automatique
  await page.getByRole("button", { name: "Publier", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Publier" }).click();
  await expect(page.getByText("v1.0.0 publiée")).toBeVisible();
  const pf = await call(page, "POST", "/forms/", { name: `Pub ${slug}`, title: `Titre ${slug}`, workflow: wf, environment: "test" });
  expect((await call(page, "POST", `/forms/${pf.data.id}/attach/`, { form: slug })).status).toBe(200);
  const anon = await browser.newContext();
  const pub = await anon.newPage();
  await pub.goto(pf.data.url);
  await expect(pub.locator(".fb-heading")).toHaveCSS("color", "rgb(185, 28, 28)");
  await expect(pub.locator(".fb-heading")).toHaveCSS("text-transform", "uppercase");
  await expect(pub.locator(".fb-actions button")).toHaveCSS("letter-spacing", /^1\.\d+px$/);
  await anon.close();
});

test("CSS visuel : une session expirée n'est pas présentée comme un CSS refusé ; les réglages restent et on peut réessayer", async ({ page }) => {
  await login(page);
  const slug = `sess-${Date.now().toString().slice(-6)}`;
  expect((await call(page, "POST", "/form-library/", { slug, name: `Session ${slug}`, definition: { version: 1, sections: [{ id: "s1", title: "T", fields: [text("a")] }] } })).status).toBe(201);
  await page.goto(`/forms/${slug}`);
  await page.getByRole("tab", { name: "Apparence" }).click();
  await page.getByRole("tab", { name: "Avancé (CSS)" }).click();
  const preview = page.getByRole("region", { name: "Aperçu du formulaire" });
  // le serveur répond « non authentifié » (session terminée)
  await page.route("**/form-library/check/", (route) => route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ error: { code: "not_authenticated", message: "Authentification requise." } }) }));
  await preview.locator("label").first().click();
  await page.getByRole("tab", { name: "Couleurs et fond" }).click();
  await page.getByLabel("Couleur du texte : code").fill("#15803d");
  await expect(page.getByRole("alert").filter({ hasText: "session a expiré" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "Non enregistré" })).toBeVisible();
  await expect(page.getByText("CSS refusé")).toHaveCount(0);
  await expect(preview.locator(".fb-field > label").first()).toHaveCSS("color", "rgb(21, 128, 61)"); // l'aperçu garde le réglage
  // la session est de retour : « Réessayer » enregistre
  await page.unroute("**/form-library/check/");
  await page.getByRole("button", { name: "Réessayer" }).click();
  await expect(page.getByRole("status").filter({ hasText: "CSS appliqué" })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("menus contextuels : ils restent dans la fenêtre (pas de défilement horizontal), même près du bord droit", async ({ page }) => {
  await login(page);
  const slug = `pop-${Date.now().toString().slice(-6)}`;
  expect((await call(page, "POST", "/form-library/", { slug, name: `Menus ${slug}`, definition: { version: 1, sections: [{ id: "s1", title: "T", fields: [text("a")] }] } })).status).toBe(201);
  await page.goto(`/forms/${slug}`);
  await page.getByRole("tab", { name: "Apparence" }).click();
  await page.getByRole("tab", { name: "Avancé (CSS)" }).click();
  // le débordement se voit sur le panneau de la page (qui défile) autant que sur le document
  const overflow = () => page.evaluate(() => {
    const panel = document.querySelector(".fb-fill > .tabpanel:not([hidden])") as HTMLElement;
    return Math.max(document.documentElement.scrollWidth - document.documentElement.clientWidth, panel.scrollWidth - panel.clientWidth);
  });
  expect(await overflow()).toBeLessThanOrEqual(0);
  await page.getByRole("button", { name: "Thèmes prêts à l'emploi" }).click();
  const menu = page.getByRole("group", { name: "Thèmes prêts à l'emploi" });
  await expect(menu).toBeVisible();
  const box = (await menu.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  expect(await overflow()).toBeLessThanOrEqual(0);
  await page.keyboard.press("Escape");
  // idem pour les menus de la barre d'état, de portée et d'écran
  for (const name of [/^Règles du formulaire/, /^Écran/, /^Élément à styler/]) {
    await page.getByRole("button", { name }).click();
    expect(await overflow()).toBeLessThanOrEqual(0);
    await page.keyboard.press("Escape");
  }
});

test("un champ pleine largeur remplit sa case, un demi-champ remplit sa moitié (la limite de 560 px de l'appli ne s'y applique pas)", async ({ page, browser }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `wid-${id}`;
  const wf = `wf-${slug}`;
  expect(await publishViaApi(page, wf, { inputs: { type: "object", properties: { a: { type: "string" }, b: { type: "string" }, c: { type: "string" }, d: { type: "string" } }, required: [] }, outputs: {}, edges: [], nodes: [{ id: "n", type: "wait", name: "Pause", seconds: 1 }] })).toBe(201);
  const def = { version: 1, style: { width: "full" }, sections: [{ id: "s1", title: "T", fields: [
    text("a", { layout: { row: 1, col: 1, span: 12 } }),
    text("b", { type: "textarea", layout: { row: 2, col: 1, span: 12 } }),
    text("c", { type: "select", options: [{ value: "x", label: "X" }], layout: { row: 3, col: 1, span: 6 } }),
    text("d", { layout: { row: 3, col: 7, span: 6 } }) ] }] };
  expect((await call(page, "POST", "/form-library/", { slug, name: `Largeur ${slug}`, definition: def })).status).toBe(201);
  expect((await call(page, "POST", `/form-library/${slug}/publish/`, {})).status).toBe(201);
  const pf = await call(page, "POST", "/forms/", { name: `Pub ${slug}`, title: `T ${slug}`, workflow: wf, environment: "test" });
  expect((await call(page, "POST", `/forms/${pf.data.id}/attach/`, { form: slug })).status).toBe(200);
  const anon = await browser.newContext({ viewport: { width: 1600, height: 900 } });
  const pub = await anon.newPage();
  await pub.goto(pf.data.url);
  const w = async (label: string) => (await pub.getByLabel(label, { exact: true }).boundingBox())!.width;
  const section = (await pub.locator(".fb-grid").first().boundingBox())!.width;
  // pleine largeur : tout l'espace de la ligne, bien au-delà de 560 px
  expect(await w("A")).toBeGreaterThan(section * 0.97);
  expect(await w("A")).toBeGreaterThan(700);
  expect(await w("B")).toBeGreaterThan(section * 0.97);
  // demi-largeur : la moitié de la ligne, champs de saisie et liste déroulante compris
  expect(await w("C")).toBeGreaterThan(section * 0.45);
  expect(await w("D")).toBeGreaterThan(section * 0.45);
  expect(Math.abs((await w("C")) - (await w("D")))).toBeLessThan(4);
  await anon.close();
});

test("hauteur : une cartouche représente l'épaisseur du champ ; un texte long vaut deux champs courts, qui se rangent à côté sans perdre de place", async ({ page, browser }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `hau-${id}`;
  const wf = `wf-${slug}`;
  expect(await publishViaApi(page, wf, { inputs: { type: "object", properties: { a: { type: "string" }, b: { type: "string" }, t: { type: "string" } }, required: [] }, outputs: {}, edges: [], nodes: [{ id: "n", type: "wait", name: "Pause", seconds: 1 }] })).toBe(201);
  // deux champs courts empilés à gauche, un texte long à droite qui couvre leurs deux lignes
  const def = { version: 1, sections: [{ id: "s1", title: "T", fields: [
    text("a", { layout: { row: 1, col: 1, span: 6 } }), text("b", { layout: { row: 2, col: 1, span: 6 } }),
    text("t", { type: "textarea", layout: { row: 1, col: 7, span: 6, height: 2 } }) ] }] };
  expect((await call(page, "POST", "/form-library/", { slug, name: `Hauteur ${slug}`, definition: def })).status).toBe(201);
  await page.goto(`/forms/${slug}`);

  // l'éditeur : la cartouche du texte long fait environ deux fois la hauteur d'un champ court
  const ca = (await card(page, "A").boundingBox())!;
  const cb = (await card(page, "B").boundingBox())!;
  const ct = (await card(page, "T").boundingBox())!;
  expect(ct.height).toBeGreaterThan(ca.height * 1.9);
  expect(Math.abs(ct.y - ca.y)).toBeLessThan(3);
  expect(Math.abs(ct.y + ct.height - (cb.y + cb.height))).toBeLessThan(3); // elle couvre exactement les deux lignes de A et B
  await expect(card(page, "T")).toHaveAttribute("aria-label", /hauteur 2/);

  // changer la hauteur : clavier (Maj + flèches), boutons, poignée du bas
  await card(page, "T").focus();
  await page.keyboard.press("Shift+ArrowDown");
  await expect(card(page, "T")).toHaveAttribute("aria-label", /hauteur 3/);
  await page.getByRole("button", { name: "Moins haut d'une ligne" }).click();
  await expect(card(page, "T")).toHaveAttribute("aria-label", /hauteur 2/);
  await page.getByRole("button", { name: "Plus haut d'une ligne" }).click();
  await page.getByRole("button", { name: "Plus haut d'une ligne" }).click();
  await expect(card(page, "T")).toHaveAttribute("aria-label", /hauteur 4/);
  await page.getByRole("button", { name: "Moins haut d'une ligne" }).click();
  await page.getByRole("button", { name: "Moins haut d'une ligne" }).click();
  await expect(card(page, "T")).toHaveAttribute("aria-label", /hauteur 2/);
  // à la souris : la poignée du bas
  const t2 = (await card(page, "T").boundingBox())!;
  const vh = (await card(page, "T").locator(".fb-vhandle").boundingBox())!;
  await page.mouse.move(vh.x + vh.width / 2, vh.y + vh.height / 2);
  await page.mouse.down();
  await page.mouse.move(vh.x + vh.width / 2, vh.y + vh.height / 2 + 80, { steps: 6 });
  await page.mouse.up();
  await expect(card(page, "T")).toHaveAttribute("aria-label", /hauteur 3/);
  expect((await card(page, "T").boundingBox())!.height).toBeGreaterThan(t2.height);
  await page.getByRole("button", { name: "Moins haut d'une ligne" }).click();
  await expect(card(page, "T")).toHaveAttribute("aria-label", /hauteur 2/);

  // un champ long ajouté depuis la palette démarre à deux lignes ; un champ court à une
  await page.getByRole("button", { name: "Texte long", exact: true }).click();
  await expect(card(page, "Texte long")).toHaveAttribute("aria-label", /hauteur 2/);
  await page.getByRole("button", { name: "Texte court", exact: true }).click();
  await expect(card(page, "Texte court")).toHaveAttribute("aria-label", /hauteur 1/);
  await expectNoA11yViolations(page);

  // l'aperçu : le texte long occupe la hauteur des deux champs courts, sans trou
  await page.getByRole("button", { name: "Annuler", exact: true }).click();
  await page.getByRole("button", { name: "Annuler", exact: true }).click();
  await page.getByRole("tab", { name: "Aperçu" }).click();
  const a = (await page.getByLabel("A", { exact: true }).boundingBox())!;
  const b = (await page.getByLabel("B", { exact: true }).boundingBox())!;
  const t = (await page.getByLabel("T", { exact: true }).boundingBox())!;
  expect(t.height).toBeGreaterThan(a.height * 1.8);
  expect(Math.abs(t.y + t.height - (b.y + b.height))).toBeLessThan(4); // le bas du texte long s'aligne sur celui du second champ court
  expect(t.x).toBeGreaterThan(a.x + a.width);

  // enregistré, publié et affiché de même sur la page publique
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 }); // enregistrement automatique
  expect((await call(page, "GET", `/form-library/${slug}/`)).data.definition.sections[0].fields.find((f: { key: string }) => f.key === "t").layout.height).toBe(2);
  await page.getByRole("button", { name: "Publier", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Publier" }).click();
  await expect(page.getByText("v1.0.0 publiée")).toBeVisible();
  const pf = await call(page, "POST", "/forms/", { name: `Pub ${slug}`, title: `T ${slug}`, workflow: wf, environment: "test" });
  expect((await call(page, "POST", `/forms/${pf.data.id}/attach/`, { form: slug })).status).toBe(200);
  const anon = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const pub = await anon.newPage();
  await pub.goto(pf.data.url);
  const pa = (await pub.getByLabel("A", { exact: true }).boundingBox())!;
  const pb = (await pub.getByLabel("B", { exact: true }).boundingBox())!;
  const pt = (await pub.getByLabel("T", { exact: true }).boundingBox())!;
  expect(pt.height).toBeGreaterThan(pa.height * 1.8);
  expect(Math.abs(pt.y + pt.height - (pb.y + pb.height))).toBeLessThan(4);
  await anon.close();
});
