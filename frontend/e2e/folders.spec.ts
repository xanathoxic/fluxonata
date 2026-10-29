import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, goNav, login } from "./helpers";

const uniq = () => `${Date.now()}`.slice(-7);
const tools = (page: Page) => page.getByRole("toolbar", { name: "Gérer les dossiers" });

async function newFolder(page: Page, tree: string, name: string) {
  await page.getByRole("navigation", { name: tree }).getByRole("button", { name: /^Nouveau dossier/ }).click();
  const dlg = page.getByRole("dialog", { name: "Nouveau dossier" });
  await dlg.getByLabel("Nom du dossier").fill(name);
  await dlg.getByRole("button", { name: "Créer", exact: true }).click();
  await expect(dlg).toHaveCount(0);
}

test("workflows : créer des dossiers vides, y ranger un workflow, renommer, refuser la suppression d'un dossier plein", async ({ page }) => {
  const id = uniq();
  const tree = "Dossiers de workflows";
  await login(page);
  await goNav(page, "Workflows");
  const nav = page.getByRole("navigation", { name: tree });

  await newFolder(page, tree, `Equipe ${id}`);
  const root = nav.getByRole("button", { name: new RegExp(`Equipe ${id} \\(0\\)`) });
  await expect(root).toHaveAttribute("aria-current", "true"); // le nouveau dossier est sélectionné
  await newFolder(page, tree, `Sous${id}`); // créé DANS le dossier sélectionné
  await expect(nav.getByRole("button", { name: new RegExp(`^Sous${id} \\(0\\)`) })).toHaveAttribute("aria-current", "true");
  await expectNoA11yViolations(page);

  // un dossier vide persiste après rechargement
  await page.reload();
  await nav.getByRole("button", { name: new RegExp(`^Sous${id} \\(0\\)`) }).click(); // la sélection ne survit pas au rechargement : on la refait

  // on y crée un workflow : le dossier n'est plus vide
  await page.getByRole("button", { name: "Nouveau workflow" }).click();
  await page.getByLabel("Nom du nouveau workflow").fill(`Rangé ${id}`);
  await page.getByRole("button", { name: "Créer et ouvrir l'éditeur" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(`Rangé ${id}`);
  await goNav(page, "Workflows");
  await nav.getByRole("button", { name: new RegExp(`^Sous${id} \\(1\\)`) }).click();
  await expect(page.getByRole("link", { name: `Rangé ${id}`, exact: true })).toBeVisible();

  // suppression refusée tant que le dossier contient quelque chose
  await tools(page).getByRole("button", { name: "Supprimer le dossier sélectionné" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Supprimer le dossier" }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText("n'est pas vide");
  await page.keyboard.press("Escape");

  // renommer le parent déplace tout ce qui est dessous
  await nav.getByRole("button", { name: new RegExp(`^Equipe ${id} \\(`) }).click();
  await tools(page).getByRole("button", { name: "Renommer ou déplacer le dossier sélectionné" }).click();
  const dlg = page.getByRole("dialog", { name: /Renommer ou déplacer/ });
  await dlg.getByLabel("Chemin du dossier").fill(`Pôle ${id}/Equipe`);
  await dlg.getByRole("button", { name: "Enregistrer" }).click();
  await expect(nav.getByRole("button", { name: new RegExp(`^Equipe ${id}`) })).toHaveCount(0); // la liste des workflows est relue : plus aucune trace de l'ancien chemin
  await expect(nav.getByRole("button", { name: /^Equipe \(1\)/ }).first()).toBeVisible();
  await nav.getByRole("button", { name: new RegExp(`^Sous${id} \\(1\\)`) }).click();
  await expect(page.getByRole("row", { name: new RegExp(`Rangé ${id}`) })).toContainText(`Pôle ${id}/Equipe/Sous${id}`);
  await expect(nav.getByRole("button", { name: new RegExp(`^Equipe ${id}`) })).toHaveCount(0);

  // nettoyage : on supprime le workflow (jamais exécuté) puis les dossiers vides
  const slug = await page.evaluate(async (name) => {
    const r = await (await fetch(`/api/v1/workspaces/demo/workflows/?limit=200&q=${encodeURIComponent(name)}`)).json();
    return (r.results as { slug: string; name: string }[]).find((w) => w.name === name)?.slug;
  }, `Rangé ${id}`);
  await page.evaluate(async (s) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    await fetch(`/api/v1/workspaces/demo/workflows/${s}/delete/`, { method: "DELETE", headers: { "X-CSRFToken": csrf } });
  }, slug);
  await page.reload();
  await nav.getByRole("button", { name: new RegExp(`^Pôle ${id}`) }).click();
  await tools(page).getByRole("button", { name: "Supprimer le dossier sélectionné" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Supprimer le dossier" }).click();
  await expect(nav.getByRole("button", { name: new RegExp(`Pôle ${id}`) })).toHaveCount(0);
});

for (const t of [
  { name: "actions", tree: "Dossiers d'actions", go: async (p: Page) => { await goNav(p, "Bibliothèques"); } },
  { name: "skills IA", tree: "Dossiers de skills", go: async (p: Page) => { await goNav(p, "Bibliothèques"); await p.getByRole("list", { name: "Bibliothèques" }).getByRole("link", { name: "Skills IA", exact: true }).click(); } },
]) {
  test(`${t.name} : créer, sélectionner, renommer et supprimer un dossier vide ; lecture seule sans les droits`, async ({ page, browser }) => {
    const id = uniq();
    await login(page);
    await t.go(page);
    const nav = page.getByRole("navigation", { name: t.tree });
    await newFolder(page, t.tree, `Dossier ${id}`);
    await expect(nav.getByRole("button", { name: new RegExp(`Dossier ${id} \\(0\\)`) })).toHaveAttribute("aria-current", "true");
    await tools(page).getByRole("button", { name: "Renommer ou déplacer le dossier sélectionné" }).click();
    await page.getByRole("dialog").getByLabel("Chemin du dossier").fill(`Renommé ${id}/Niveau 2`);
    await page.getByRole("dialog").getByRole("button", { name: "Enregistrer" }).click();
    await expect(nav.getByRole("button", { name: /^Niveau 2 \(0\)/ })).toBeVisible();
    await expectNoA11yViolations(page);
    await tools(page).getByRole("button", { name: "Supprimer le dossier sélectionné" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Supprimer le dossier" }).click();
    await nav.getByRole("button", { name: new RegExp(`^Renommé ${id}`) }).click();
    await tools(page).getByRole("button", { name: "Supprimer le dossier sélectionné" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Supprimer le dossier" }).click();
    await expect(nav.getByRole("button", { name: new RegExp(`Renommé ${id}`) })).toHaveCount(0);

    // un lecteur voit l'arbre mais n'a aucun outil de gestion
    const viewer = await (await browser.newContext()).newPage();
    await login(viewer, "bob");
    await t.go(viewer);
    await expect(viewer.getByRole("navigation", { name: t.tree })).toBeVisible();
    await expect(viewer.getByRole("toolbar", { name: "Gérer les dossiers" })).toHaveCount(0);
  });
}
