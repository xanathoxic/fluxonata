import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, login, publishViaApi } from "./helpers";

const uniq = () => `${Date.now()}`.slice(-7);

// Ce qu'un brouillon IA contient quand l'assistant a écrit lui-même un script (état mis en place par l'API,
// ce que le test vérifie est la relecture humaine obligatoire).
const draft = {
  inputs: { type: "object", properties: { texte: { type: "string", title: "Texte" } }, required: ["texte"] },
  nodes: [{ id: "etape_1", type: "script", name: "Compter les mots", language: "python", script: "nb = len(texte.split())\nSystem.log(nb)\n",
    bindings: { texte: "{{ inputs.texte }}" }, outputs: [{ name: "nb", type: "integer" }], timeout_seconds: 60, ai_review: true, ui: { x: 200, y: 120 } }],
  edges: [], outputs: {},
};

test("script écrit par l'IA : publication bloquée jusqu'à la relecture humaine", async ({ page }) => {
  const slug = `ia-script-${uniq()}`;
  await login(page);
  expect(await publishViaApi(page, slug, draft)).toBeGreaterThanOrEqual(400); // refusé : code non relu

  await page.goto(`/workflows/${slug}/schema`);
  await page.locator(".flow-node", { hasText: "Compter les mots" }).click();
  await expect(page.getByText(/Script écrit par l'IA : relisez le code/).first()).toBeVisible();
  await page.getByRole("tab", { name: "Script" }).click();
  await page.getByRole("button", { name: "Ouvrir l'éditeur de script" }).click();
  const dialog = page.getByRole("dialog", { name: /Éditeur de script/ });
  await expect(dialog.getByRole("note")).toContainText("Code écrit par l'IA, non relu");
  await expectNoA11yViolations(page);
  await dialog.getByRole("button", { name: "J'ai relu ce code" }).click();
  await expect(dialog.getByRole("note")).toHaveCount(0);
  await dialog.getByRole("button", { name: "Fermer l'éditeur" }).click();

  await expect(page.getByText(/Script écrit par l'IA : relisez le code/)).toHaveCount(0);
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 15_000 }); // enregistrement automatique
  const status = await page.evaluate(async (s) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    return (await fetch(`/api/v1/workspaces/demo/workflows/${s}/publish/`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: "{}" })).status;
  }, slug);
  expect(status).toBeLessThan(300); // relu : publiable
});
