import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, login, publishViaApi } from "./helpers";

async function call(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ({ method, path, body }) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    const r = await fetch(`/api/v1/workspaces/demo${path}`, { method, headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: body ? JSON.stringify(body) : undefined });
    return { status: r.status, data: await r.json().catch(() => ({})) };
  }, { method, path, body });
}
const f = (key: string, label: string, extra: Record<string, unknown> = {}) => ({ key, label, type: "text", ...extra });
const when = (field: string, op: string, extra: Record<string, unknown> = {}) => ({ field, op, ...extra });
const card = (page: Page, name: string) => page.getByRole("button", { name: new RegExp(`^${name}( \\*)? [^,]*, ligne`) });
const KEYS = ["pays", "email", "conf", "tva", "ville", "code"];
const wfGraph = { inputs: { type: "object", properties: Object.fromEntries(KEYS.map((k) => [k, { type: "string" }])), required: [] }, outputs: {}, edges: [], nodes: [{ id: "n", type: "wait", name: "Pause", seconds: 1 }] };

test("éditeur de conditions : fenêtre centrée, conditions riches, groupes, comparaison à un champ, effets, essai en direct", async ({ page }) => {
  await login(page);
  const slug = `rl-${Date.now().toString().slice(-6)}`;
  const def = { version: 1, sections: [{ id: "s1", title: "T", fields: [
    f("pays", "Pays", { type: "select", options: [{ value: "FR", label: "France" }, { value: "BE", label: "Belgique" }, { value: "DE", label: "Allemagne" }] }),
    f("email", "E-mail", { type: "email" }),
    f("conf", "Confirmation"),
    f("tva", "Numéro de TVA"),
  ] }] };
  expect((await call(page, "POST", "/form-library/", { slug, name: `Règles ${slug}`, definition: def })).status).toBe(201);
  await page.goto(`/forms/${slug}`);
  await card(page, "Numéro de TVA").click();
  await page.getByRole("button", { name: "Ajouter des conditions" }).click();

  // la fenêtre est au milieu de l'écran
  const dlg = page.getByRole("dialog", { name: "Conditions du champ « Numéro de TVA »" });
  await expect(dlg).toBeVisible();
  const box = (await dlg.boundingBox())!;
  const vp = page.viewportSize()!;
  expect(Math.abs(box.x + box.width / 2 - vp.width / 2)).toBeLessThan(30);
  expect(Math.abs(box.y + box.height / 2 - vp.height / 2)).toBeLessThan(80);

  // une règle « Afficher seulement si… » : Pays est égal à Belgique
  await dlg.getByRole("button", { name: "Afficher seulement si…" }).click();
  await dlg.getByLabel("Condition 1 : champ").selectOption({ label: "Pays" });
  await dlg.getByLabel("Condition 1 : valeur").selectOption("BE");
  await expect(dlg.getByRole("status", { name: "Résumé de la règle" })).toContainText("Pays est égal à « BE »");
  await expect(dlg.getByRole("status", { name: "Résumé de la règle" })).toContainText("afficher le champ");
  await expect(dlg.getByRole("status", { name: "Résumé de la règle" })).toContainText("masquer le champ"); // le « sinon » est écrit par le modèle

  // OU une seconde condition : l'e-mail finit par « .be », sans tenir compte de la casse
  await dlg.getByRole("radio", { name: "OU" }).click();
  await dlg.getByRole("button", { name: "Condition", exact: true }).click();
  await dlg.getByLabel("Condition 2 : champ").selectOption({ label: "E-mail" });
  await dlg.getByLabel("Condition 2 : opérateur").selectOption({ label: "finit par" });
  await dlg.getByLabel("Condition 2 : valeur").fill(".BE");
  await dlg.getByRole("button", { name: "Condition 2 : ignorer la casse" }).click();
  await expect(dlg.getByRole("status", { name: "Résumé de la règle" })).toContainText("Pays est égal à « BE » OU E-mail finit par « .BE » (sans tenir compte de la casse)");

  // comparer à la valeur d'un autre champ
  await dlg.getByRole("button", { name: "Condition", exact: true }).click();
  await dlg.getByLabel("Condition 3 : champ").selectOption({ label: "Confirmation" });
  await dlg.getByLabel("Condition 3 : opérateur").selectOption({ label: "est égal à" });
  await dlg.getByRole("group", { name: "Condition 3", exact: true }).getByRole("radio", { name: "La valeur d'un autre champ" }).click();
  await dlg.getByLabel("Condition 3 : autre champ").selectOption({ label: "E-mail" });
  await expect(dlg.getByRole("status", { name: "Résumé de la règle" })).toContainText("Confirmation est égal à la valeur de « E-mail »");

  // un groupe imbriqué
  await dlg.getByRole("button", { name: "Groupe", exact: true }).click();
  await expect(dlg.getByRole("group", { name: "Groupe 4" })).toBeVisible();

  // des effets : obligatoire et un texte avec un {champ}
  await dlg.getByRole("button", { name: "Ajouter : Rendre obligatoire" }).first().click();
  await dlg.getByRole("button", { name: "Ajouter : Changer un texte" }).first().click();
  await dlg.getByLabel("Alors : effet 3 : nouveau texte").fill("TVA de {pays}");
  await expect(dlg.getByRole("status", { name: "Résumé de la règle" })).toContainText("libellé : « TVA de {pays} »");
  await expect(dlg.getByRole("button", { name: "Ajouter : Limiter les choix" }).first()).toBeDisabled(); // un champ texte n'a pas de liste

  // essayer : la condition réagit aux valeurs saisies
  const tryout = dlg.getByRole("region", { name: "Essayer la règle" });
  await tryout.getByText("Pays", { exact: true }).locator("..").locator("select").selectOption("BE");
  await expect(tryout.getByText("La condition est vraie")).toBeVisible();
  // deux champs vides sont égaux : la condition « Confirmation = E-mail » est alors vraie ; avec deux valeurs différentes, elle est fausse
  await tryout.getByText("Pays", { exact: true }).locator("..").locator("select").selectOption("DE");
  await expect(tryout.getByText("La condition est vraie")).toBeVisible();
  await tryout.locator("label", { hasText: "E-mail" }).locator("input").fill("ada@x.fr");
  await tryout.locator("label", { hasText: "Confirmation" }).locator("input").fill("autre");
  await expect(tryout.getByText("La condition est fausse")).toBeVisible();
  await expect(tryout).toContainText("masqué");
  await expectNoA11yViolations(page);

  // appliquer : le champ porte la règle ; « Annuler » n'aurait rien changé
  await dlg.getByRole("button", { name: "Appliquer" }).click();
  await expect(dlg).toHaveCount(0);
  await expect(page.getByText(/^1\. Quand Pays est égal/)).toBeVisible();
  await expect(page.getByRole("button", { name: /Gérer les conditions \(1\)/ })).toBeVisible();
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 }); // enregistrement automatique
  const saved = (await call(page, "GET", `/form-library/${slug}/`)).data.definition.sections[0].fields.find((x: { key: string }) => x.key === "tva");
  expect(saved.rules).toHaveLength(1);
  expect(saved.rules[0].when).toMatchObject({ op: "any" });
  expect(saved.rules[0].then.map((e: { do: string }) => e.do)).toEqual(["show", "require", "text"]);
});

test("règles à l'exécution : afficher, obligatoire, textes, choix limités, comparaisons ; le serveur applique les mêmes règles", async ({ page, browser }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const slug = `rt-${id}`;
  const wf = `wf-${slug}`;
  expect(await publishViaApi(page, wf, wfGraph)).toBe(201);
  const def = { version: 1, sections: [{ id: "s1", title: "T", fields: [
    f("pays", "Pays", { type: "select", required: true, options: [{ value: "FR", label: "France" }, { value: "BE", label: "Belgique" }] }),
    f("email", "E-mail"),
    f("conf", "Confirmation", { rules: [{ when: when("email", "not_empty"), then: [{ do: "text", target: "label", text: "Confirmez {email}" }, { do: "text", target: "help", text: "Pays : {pays}" }] }] }),
    f("tva", "TVA", { rules: [{ when: when("pays", "equals", { value: "be", ci: true }), then: [{ do: "show" }, { do: "require" }], else: [{ do: "hide" }] }] }),
    f("ville", "Ville", { type: "select", options: [{ value: "paris", label: "Paris" }, { value: "lyon", label: "Lyon" }, { value: "bruxelles", label: "Bruxelles" }],
      rules: [{ when: when("pays", "equals", { value: "FR" }), then: [{ do: "options", values: ["paris", "lyon"] }], else: [{ do: "options", values: ["bruxelles"] }] }] }),
    f("code", "Code société", { rules: [{ when: { op: "all", items: [when("email", "contains", { value: "@CORP", ci: true }), when("email", "ends_with", { value: ".com" })] }, then: [{ do: "show" }], else: [{ do: "hide" }] }] }),
  ] }] };
  expect((await call(page, "POST", "/form-library/", { slug, name: `Exéc ${slug}`, definition: def })).status).toBe(201);
  expect((await call(page, "POST", `/form-library/${slug}/publish/`, {})).status).toBe(201);
  const pf = await call(page, "POST", "/forms/", { name: `Pub ${slug}`, title: `T ${slug}`, workflow: wf, environment: "test" });
  expect((await call(page, "POST", `/forms/${pf.data.id}/attach/`, { form: slug })).status).toBe(200);

  const anon = await browser.newContext();
  const pub = await anon.newPage();
  await pub.goto(pf.data.url);
  await expect(pub.getByLabel("Pays")).toBeVisible();
  // au départ : aucun pays, la TVA est masquée, le code aussi
  await expect(pub.getByLabel(/^TVA/)).toHaveCount(0);
  await expect(pub.getByLabel("Code société")).toHaveCount(0);
  // France : la TVA reste masquée, la liste des villes se limite
  await pub.getByLabel("Pays").selectOption("FR");
  await expect(pub.getByLabel(/^TVA/)).toHaveCount(0);
  await expect(pub.getByLabel("Ville").locator("option")).toHaveText(["Choisir…", "Paris", "Lyon"]);
  // Belgique : la TVA apparaît, obligatoire ; une autre liste de villes
  await pub.getByLabel("Pays").selectOption("BE");
  await expect(pub.getByLabel(/^TVA/)).toBeVisible();
  await expect(pub.getByLabel("Ville").locator("option")).toHaveText(["Choisir…", "Bruxelles"]);
  await pub.getByRole("button", { name: "Envoyer" }).click();
  await expect(pub.getByRole("alert").filter({ hasText: "obligatoire" })).toBeVisible();
  // des textes qui suivent les autres champs
  await expect(pub.getByLabel("Confirmation")).toBeVisible();
  await pub.getByLabel("E-mail").fill("ada@Corp.com");
  await expect(pub.getByLabel("Confirmez ada@Corp.com")).toBeVisible();
  await expect(pub.getByText("Pays : BE")).toBeVisible();
  // des conditions combinées, sans tenir compte de la casse
  await expect(pub.getByLabel("Code société")).toBeVisible();
  await pub.getByLabel("E-mail").fill("ada@corp.fr");
  await expect(pub.getByLabel("Code société")).toHaveCount(0);
  await pub.getByLabel("E-mail").fill("ada@gmail.com");
  await expect(pub.getByLabel("Code société")).toHaveCount(0);

  // le serveur applique les mêmes règles, même quand on contourne la page
  const post = (values: Record<string, unknown>) => pub.evaluate(async ({ url, values }) => {
    const r = await fetch(`/api/v1${url.replace("/f/", "/forms/")}/`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values }) });
    return { status: r.status, body: await r.json().catch(() => ({})) };
  }, { url: pf.data.url, values });
  const noTva = await post({ pays: "BE", ville: "bruxelles" });
  expect(noTva.status).toBe(400);
  expect(JSON.stringify(noTva.body)).toContain("tva");
  const wrongCity = await post({ pays: "FR", ville: "bruxelles" });
  expect(wrongCity.status).toBe(400);
  expect(JSON.stringify(wrongCity.body)).toContain("Choix non proposé");
  expect((await post({ pays: "FR", ville: "lyon", tva: "ignorée" })).status).toBe(201); // la TVA masquée est écartée, pas refusée
  expect((await post({ pays: "BE", ville: "bruxelles", tva: "BE0123" })).status).toBe(201);
  await anon.close();
});

test("conditions : une ancienne règle d'affichage est reprise comme première règle, à convertir", async ({ page }) => {
  await login(page);
  const slug = `lg-${Date.now().toString().slice(-6)}`;
  const def = { version: 1, sections: [{ id: "s1", title: "T", fields: [f("pays", "Pays"), f("tva", "TVA", { visible_if: "values.pays == 'BE'" })] }] };
  expect((await call(page, "POST", "/form-library/", { slug, name: `Ancien ${slug}`, definition: def })).status).toBe(201);
  await page.goto(`/forms/${slug}`);
  await card(page, "TVA").click();
  await expect(page.getByText("Règle d'affichage :")).toBeVisible();
  await page.getByRole("button", { name: /Gérer les conditions \(1\)/ }).click();
  const dlg = page.getByRole("dialog", { name: /Conditions du champ/ });
  await expect(dlg.getByRole("status", { name: "Résumé de la règle" })).toContainText("Pays est égal à « BE »");
  await expect(dlg.getByText("L'ancienne règle d'affichage de ce champ a été reprise")).toBeVisible();
  await dlg.getByRole("button", { name: "Appliquer" }).click();
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 }); // enregistrement automatique
  const saved = (await call(page, "GET", `/form-library/${slug}/`)).data.definition.sections[0].fields.find((x: { key: string }) => x.key === "tva");
  expect(saved.visible_if).toBeUndefined();
  expect(saved.rules[0]).toMatchObject({ then: [{ do: "show" }], else: [{ do: "hide" }] });
});

test("conditions entre champs côte à côte : le champ de gauche peut lire celui de droite, sans boucle", async ({ page }) => {
  await login(page);
  const slug = `rc-${Date.now().toString().slice(-6)}`;
  const def = { version: 1, sections: [{ id: "s1", title: "T", fields: [
    f("ville", "Ville", { layout: { row: 1, col: 1, span: 6 } }),
    f("code", "Code postal", { layout: { row: 1, col: 7, span: 6 } }),
  ] }] };
  expect((await call(page, "POST", "/form-library/", { slug, name: `Côte à côte ${slug}`, definition: def })).status).toBe(201);
  await page.goto(`/forms/${slug}`);

  // « Ville » (à gauche) lit « Code postal » (à droite)
  await card(page, "Ville").click();
  await page.getByRole("button", { name: "Ajouter des conditions" }).click();
  const dlg = page.getByRole("dialog", { name: "Conditions du champ « Ville »" });
  await dlg.getByRole("button", { name: "Masquer si…" }).click();
  await dlg.getByLabel("Condition 1 : champ").selectOption({ label: "Code postal" });
  await dlg.getByLabel("Condition 1 : opérateur").selectOption({ label: "commence par" });
  await dlg.getByLabel("Condition 1 : valeur").fill("99");
  await dlg.getByRole("button", { name: "Appliquer" }).click();
  await expect(dlg).toBeHidden();

  // « Code postal » ne peut plus lire « Ville » : ce serait une boucle
  await card(page, "Code postal").click();
  await page.getByRole("button", { name: "Ajouter des conditions" }).click();
  const dlg2 = page.getByRole("dialog", { name: "Conditions du champ « Code postal »" });
  await expect(dlg2.getByText("il n'y en a pas encore")).toBeVisible();
  await dlg2.getByRole("button", { name: "Annuler" }).click();
});

test("règle « Remplir le champ » : la valeur est mise par la règle et le champ n'est plus modifiable", async ({ page }) => {
  await login(page);
  const slug = `rf-${Date.now().toString().slice(-6)}`;
  const def = { version: 1, sections: [{ id: "s1", title: "T", fields: [
    f("pays", "Pays", { layout: { row: 1, col: 1, span: 6 } }),
    f("ville", "Ville", { layout: { row: 1, col: 7, span: 6 } }),
  ] }] };
  expect((await call(page, "POST", "/form-library/", { slug, name: `Remplir ${slug}`, definition: def })).status).toBe(201);
  await page.goto(`/forms/${slug}`);
  await card(page, "Ville").click();
  await page.getByRole("button", { name: "Ajouter des conditions" }).click();
  const dlg = page.getByRole("dialog", { name: "Conditions du champ « Ville »" });
  await dlg.getByRole("button", { name: "Changer un texte si…" }).click();
  await dlg.getByLabel("Condition 1 : champ").selectOption({ label: "Pays" });
  await dlg.getByLabel("Condition 1 : valeur").fill("FR");
  await dlg.getByRole("button", { name: "Supprimer alors : effet 1" }).click();
  await dlg.getByRole("button", { name: "Ajouter : Remplir le champ" }).first().click();
  await dlg.getByLabel("Alors : effet 1 : valeur à mettre").fill("Paris ({pays})");
  await expect(dlg.getByRole("status", { name: "Résumé de la règle" })).toContainText("remplir le champ avec « Paris ({pays}) »");
  await dlg.getByRole("button", { name: "Appliquer" }).click();
  await page.getByRole("tab", { name: "Aperçu" }).click();
  await page.getByLabel("Pays").fill("FR");
  await expect(page.getByLabel("Ville")).toHaveValue("Paris (FR)");
  await expect(page.getByLabel("Ville")).toHaveAttribute("readonly", "");
  await page.getByLabel("Pays").fill("DE");
  await expect(page.getByLabel("Ville")).not.toHaveAttribute("readonly", "");
});

test("valeur composée dans une condition : « {prénom} {nom} » comparé à un texte", async ({ page }) => {
  await login(page);
  const slug = `rv-${Date.now().toString().slice(-6)}`;
  const def = { version: 1, sections: [{ id: "s1", title: "T", fields: [
    f("prenom", "Prénom", { layout: { row: 1, col: 1, span: 6 } }),
    f("nom", "Nom", { layout: { row: 1, col: 7, span: 6 } }),
    f("note", "Note", { layout: { row: 2, col: 1, span: 12 } }),
  ] }] };
  expect((await call(page, "POST", "/form-library/", { slug, name: `Composée ${slug}`, definition: def })).status).toBe(201);
  await page.goto(`/forms/${slug}`);
  await card(page, "Note").click();
  await page.getByRole("button", { name: "Ajouter des conditions" }).click();
  const dlg = page.getByRole("dialog", { name: "Conditions du champ « Note »" });
  await dlg.getByRole("button", { name: "Changer un texte si…" }).click();
  await dlg.getByLabel("Condition 1 : champ").selectOption({ label: "Valeur composée…" });
  await dlg.getByLabel("Condition 1 : valeur composée").fill("{prenom} {nom}");
  await dlg.getByLabel("Condition 1 : valeur", { exact: true }).fill("ada lovelace");
  await dlg.getByRole("button", { name: "Condition 1 : ignorer la casse" }).click();
  await expect(dlg.getByRole("status", { name: "Résumé de la règle" })).toContainText("la valeur composée « {Prénom} {Nom} » est égal à « ada lovelace » (sans tenir compte de la casse)");
  await dlg.getByRole("button", { name: "Supprimer alors : effet 1" }).click();
  await dlg.getByRole("button", { name: "Ajouter : Remplir le champ" }).first().click();
  await dlg.getByLabel("Alors : effet 1 : valeur à mettre").fill("Bienvenue {prenom}");
  await dlg.getByRole("button", { name: "Appliquer" }).click();
  await page.getByRole("tab", { name: "Aperçu" }).click();
  await page.getByLabel("Prénom").fill("Ada");
  await page.getByLabel("Nom", { exact: true }).fill("LOVELACE");
  await expect(page.getByLabel("Note")).toHaveValue("Bienvenue Ada");
  await page.getByLabel("Nom", { exact: true }).fill("Byron");
  await expect(page.getByLabel("Note")).not.toHaveAttribute("readonly", "");
});

test("champ calculé : formule, essai en direct, valeur recalculée et non modifiable", async ({ page }) => {
  await login(page);
  const slug = `rk-${Date.now().toString().slice(-6)}`;
  const n = (key: string, label: string, extra: Record<string, unknown> = {}) => f(key, label, { type: "number", ...extra });
  const def = { version: 1, sections: [{ id: "s1", title: "T", fields: [
    n("prix", "Prix", { layout: { row: 1, col: 1, span: 6 } }),
    n("qte", "Quantité", { layout: { row: 1, col: 7, span: 6 } }),
    n("total", "Total TTC", { layout: { row: 2, col: 1, span: 12 } }),
  ] }] };
  expect((await call(page, "POST", "/form-library/", { slug, name: `Calcul ${slug}`, definition: def })).status).toBe(201);
  await page.goto(`/forms/${slug}`);
  await card(page, "Total TTC").click();
  // le calcul se définit dans la même fenêtre que les conditions, onglet « Calcul »
  await page.getByRole("button", { name: "Ajouter des conditions" }).click();
  const dlg = page.getByRole("dialog", { name: "Conditions du champ « Total TTC »" });
  await expect(dlg).toBeVisible();
  await dlg.getByRole("tab", { name: /^Calcul/ }).click();
  const box = (await dlg.boundingBox())!;
  expect(Math.abs(box.x + box.width / 2 - page.viewportSize()!.width / 2)).toBeLessThan(30);
  await dlg.getByRole("button", { name: "Prix", exact: true }).click();
  await dlg.getByLabel("Formule").pressSequentially(" * ");
  await dlg.getByRole("button", { name: "Quantité", exact: true }).click();
  await dlg.getByLabel("Formule").pressSequentially(" * 1.2");
  await expect(dlg.getByLabel("Formule")).toHaveValue("{prix} * {qte} * 1.2");
  await expect(dlg.getByRole("status").first()).toHaveText("Formule valide.");
  await dlg.getByLabel("Prix", { exact: true }).fill("10");
  await dlg.getByLabel("Quantité", { exact: true }).fill("3");
  await expect(dlg.getByRole("status", { name: "Résultat de l'essai" })).toContainText("36");
  await dlg.getByLabel("Formule").fill("{prix} *");
  await expect(dlg.getByRole("button", { name: "Appliquer" })).toBeDisabled();
  await expectNoA11yViolations(page);
  await dlg.getByLabel("Formule").fill("round({prix} * {qte} * 1.2, 2)");
  await expect(dlg.getByRole("tab", { name: "Calcul ✓" })).toBeVisible();
  await dlg.getByRole("button", { name: "Appliquer" }).click();
  // le calcul est résumé dans « Conditions, règles et calcul » : plus de bouton séparé
  await expect(page.getByText("Calcul :")).toBeVisible();
  await expect(page.getByRole("button", { name: "Définir un calcul" })).toHaveCount(0);
  await page.getByRole("button", { name: "Gérer les conditions et le calcul" }).click();
  const again = page.getByRole("dialog", { name: "Conditions du champ « Total TTC »" });
  await again.getByRole("tab", { name: /^Calcul/ }).click();
  await expect(again.getByLabel("Formule")).toHaveValue("round({prix} * {qte} * 1.2, 2)");
  await again.getByRole("button", { name: "Annuler" }).click();

  await page.getByRole("tab", { name: "Aperçu" }).click();
  await page.getByLabel("Prix", { exact: true }).fill("2.5");
  await page.getByLabel("Quantité", { exact: true }).fill("4");
  await expect(page.getByLabel("Total TTC")).toHaveValue("12");
  await expect(page.getByLabel("Total TTC")).toHaveAttribute("readonly", "");
  await page.getByLabel("Quantité", { exact: true }).fill("");
  await expect(page.getByLabel("Total TTC")).toHaveValue("");
});
