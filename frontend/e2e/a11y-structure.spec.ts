import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { login } from "./helpers";

/** Structure exposée aux technologies d'assistance, vérifiée par le navigateur (arbre d'accessibilité et règles « bonnes pratiques »
 *  d'axe : un titre de niveau 1, un repère principal…). Ce n'est PAS un essai avec un lecteur d'écran : il ne dit rien de la
 *  qualité de l'annonce vocale. */

const ROUTES = ["/", "/catalog", "/workflows", "/library/actions", "/library/forms", "/library/controls", "/connections", "/triggers", "/approvals", "/executions", "/ops", "/admin", "/account", "/forms"];

async function structure(page: Page, where: string) {
  const h1 = page.locator("main h1").first();
  await expect(h1, `${where} : un titre de niveau 1`).toBeVisible();
  const heading = ((await h1.textContent()) ?? "").replace(/\s+/g, " ").trim();
  const expected = heading === "Fluxonata" ? "Fluxonata" : `${heading} — Fluxonata`; // la page de connexion porte déjà le nom de l'application
  await expect(page, `${where} : le titre du document reprend le titre de la page`).toHaveTitle(expected);
  await expect(page.locator("main")).toHaveCount(1);
  const results = await new AxeBuilder({ page }).withTags(["best-practice"]).analyze();
  const found = results.violations.map((v) => `${v.id} (${v.nodes.length}) : ${v.help}`);
  expect(found, `${where} :\n${found.join("\n")}`).toEqual([]);
  return expected;
}

test("écrans principaux : un titre de document propre à chaque page, un h1, un repère principal, règles « bonnes pratiques » d'axe", async ({ page }) => {
  test.setTimeout(240_000);
  await page.goto("/");
  await expect(page.getByLabel("Identifiant")).toBeVisible();
  const titles = [await structure(page, "connexion")];
  await login(page);
  for (const route of ROUTES) {
    await page.goto(route);
    await page.waitForLoadState("networkidle").catch(() => undefined);
    titles.push(await structure(page, route));
  }
  // « / » et « /home » partagent l'accueil : on compte les titres distincts parmi les autres écrans
  const distinct = new Set(titles);
  expect(distinct.size, `titres de document : ${[...titles].join(" | ")}`).toBeGreaterThanOrEqual(ROUTES.length - 2);
});
