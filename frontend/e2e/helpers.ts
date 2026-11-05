import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

export async function login(page: Page, user = "alice") {
  await page.goto("/");
  await page.getByLabel("Identifiant").fill(user);
  await page.getByLabel("Mot de passe").fill("demo-fluxonata");
  await page.getByRole("button", { name: "Se connecter" }).click();
  await expect(page.getByRole("heading", { name: /Bonjour/ })).toBeVisible({ timeout: 20_000 }); // le hachage du mot de passe est volontairement lent
}

export async function loginToCatalog(page: Page, user = "alice") {
  await login(page, user);
  await page.getByRole("navigation", { name: "Navigation principale" }).getByRole("link", { name: "Catalogue" }).click();
  await expect(page.getByRole("heading", { name: "Catalogue d'automatisations" })).toBeVisible();
}

export async function expectNoA11yViolations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const summary = results.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.length}) → ${v.nodes.slice(0, 4).map((n) => `${n.target.join(" ")} [${(n.any[0]?.message ?? n.failureSummary ?? "").replace(/\s+/g, " ").slice(0, 160)}]`).join(" | ")}`);
  expect(summary, summary.join("\n")).toEqual([]);
}

/** Sidebar navigation; on phones the menu is collapsed behind a "Menu" button. */
export async function goNav(page: Page, name: string) {
  const menu = page.getByRole("button", { name: "Menu" });
  if (await menu.isVisible()) {
    if ((await menu.getAttribute("aria-expanded")) !== "true") await menu.click();
  }
  await page.getByRole("navigation", { name: "Navigation principale" }).getByRole("link", { name, exact: true }).click();
}

/** Create + publish a one-step workflow through the API (state set-up only, never what a test asserts). */
export async function publishViaApi(page: Page, slug: string, graph: unknown) {
  return page.evaluate(async ({ slug, graph }) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    const h = { "Content-Type": "application/json", "X-CSRFToken": csrf };
    const base = "/api/v1/workspaces/demo";
    await fetch(`${base}/workflows/`, { method: "POST", headers: h, body: JSON.stringify({ slug, name: `Test ${slug}` }) });
    await fetch(`${base}/workflows/${slug}/draft/`, { method: "PUT", headers: h, body: JSON.stringify({ graph, revision: 1 }) });
    return (await fetch(`${base}/workflows/${slug}/publish/`, { method: "POST", headers: h, body: "{}" })).status;
  }, { slug, graph });
}

/** Opens a dropdown menu (button `menu`) and returns the menu item `item` ; click it or assert on it. */
export async function openMenu(page: Page, menu: string, item: string) {
  const trigger = page.getByRole("button", { name: menu, exact: true });
  if ((await trigger.getAttribute("aria-expanded")) !== "true") await trigger.click();
  return page.getByRole("menuitem", { name: item }).or(page.getByRole("menuitemcheckbox", { name: item })).first();
}
export async function menuClick(page: Page, menu: string, item: string) { await (await openMenu(page, menu, item)).click(); }
