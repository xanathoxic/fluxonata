import { expect, test } from "@playwright/test";
import { login } from "./helpers";

test.use({ colorScheme: "dark" });

test("le mode sombre du système n'change pas l'apparence : l'application reste claire", async ({ page }) => {
  await page.goto("/");
  const bg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(await bg()).not.toMatch(/rgb\((\d|1\d|2\d|3\d), /); // pas un fond sombre sur la page de connexion
  await login(page, "alice");
  const [r, g, b] = (await bg()).match(/\d+/g)!.map(Number);
  expect((r! + g! + b!) / 3).toBeGreaterThan(200); // fond clair
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe("light");
});
