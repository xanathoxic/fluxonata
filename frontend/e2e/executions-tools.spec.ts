import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, goNav, login, publishViaApi } from "./helpers";

test("exécutions : filtres, export CSV et comparaison de deux exécutions, accessibilité", async ({ page }) => {
  await login(page);
  await goNav(page, "Mes exécutions");
  await expect(page.getByLabel("Environnement")).toBeVisible();
  await page.getByLabel("Du", { exact: true }).fill("2000-01-01");
  await expect(page.getByRole("link", { name: "Exporter en CSV" })).toHaveAttribute("href", /workflow-runs\/export\/\?from=2000-01-01/);
  const compare = page.getByRole("button", { name: "Comparer les 2 exécutions cochées" });
  await expect(compare).toBeDisabled();
  const boxes = page.getByRole("checkbox", { name: /^Comparer / });
  if ((await boxes.count()) >= 2) {
    await boxes.nth(0).check();
    await boxes.nth(1).check();
    await expect(compare).toBeEnabled();
    await compare.click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Comparer deux exécutions");
    await expect(page.getByRole("status").first()).toBeVisible();
  }
  await expectNoA11yViolations(page);
});

test("exécutions : recherche, pastilles de statut et pagination, accessibilité", async ({ page }) => {
  await login(page);
  // préparation : la pagination suppose plus de 10 exécutions ; une base neuve n'en a que quelques-unes.
  // On lance donc 11 exécutions d'un workflow à une pause d'une seconde (sans conteneur), sans dépendre d'autres données.
  const slug = `pag-${Date.now().toString().slice(-6)}`;
  expect(await publishViaApi(page, slug, { inputs: { type: "object", properties: {}, required: [] }, outputs: {}, edges: [], nodes: [{ id: "a", type: "wait", name: "Pause", seconds: 1 }] })).toBeLessThan(300);
  const launched = await page.evaluate(async (s) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    const codes: number[] = [];
    for (let i = 0; i < 11; i++) {
      const r = await fetch(`/api/v1/workspaces/demo/workflows/${s}/runs/`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify({ environment: "test", inputs: {} }) });
      codes.push(r.status);
    }
    return codes;
  }, slug);
  expect(launched.every((c) => c < 300), `lancements : ${launched.join(",")}`).toBe(true);
  await goNav(page, "Mes exécutions");
  await page.getByLabel("Par page").selectOption("10");
  const nav = page.getByRole("navigation", { name: "Pagination des exécutions" });
  await expect(nav).toBeVisible();
  const info = nav.getByRole("status");
  await expect(info).toContainText(/^1–10 sur \d+/);
  const next = nav.getByRole("button", { name: "Page suivante" });
  await expect(next).toBeEnabled(); // plus de 10 exécutions : une seconde page existe
  await next.click();
  await expect(info).toContainText(/^11–/);
  await expect(nav.getByRole("button", { name: "Page 2", exact: true })).toHaveAttribute("aria-current", "page");
  // une recherche sans résultat propose d'effacer les filtres ; la pastille Réussies filtre
  await page.getByLabel("Rechercher", { exact: true }).fill("zzz-introuvable-zzz");
  await expect(page.getByText("Aucune exécution ne correspond à ces filtres")).toBeVisible();
  await expect(page.getByText("Texte : « zzz-introuvable-zzz »")).toBeVisible();
  await page.getByRole("button", { name: "Tout effacer" }).first().click();
  await page.getByRole("button", { name: /^Réussies/ }).click();
  await expect(page.getByRole("button", { name: /^Réussies/ })).toHaveAttribute("aria-pressed", "true");
  await expectNoA11yViolations(page);
});
