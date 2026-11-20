import { expect, test } from "@playwright/test";
import { boxOf, login } from "./helpers";

/** La barre du haut et le menu de gauche restent affichés quand la page défile (ordinateur). */
test("au défilement, la barre du haut reste en haut et le menu de gauche reste visible sous elle", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 700 });
  await login(page, "alice");
  await page.goto("/catalog");
  const bar = page.locator("header.topbar");
  await expect(bar).toBeVisible();
  await page.evaluate(() => {
    const filler = document.createElement("div");
    filler.style.height = "3000px";
    document.querySelector("main")?.appendChild(filler);
  });
  await page.evaluate(() => window.scrollTo(0, 1500));
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(1000);
  const barBox = await boxOf(bar);
  const sideBox = await boxOf(page.locator("nav#sidebar"));
  expect(barBox.y).toBe(0);
  expect(sideBox.y).toBeCloseTo(barBox.height, 0);
  expect(sideBox.y + sideBox.height).toBeLessThanOrEqual(700);
  await expect(page.getByRole("link", { name: "Accueil" })).toBeInViewport();
});
