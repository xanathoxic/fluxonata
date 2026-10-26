// Task scenarios. Success = scripted user reaches the goal; durations are real measurements.
// This is an evaluation by scenarios, NOT a user study with real participants.
import { expect, test } from "@playwright/test";
import { goNav, login } from "./helpers";

test("S1 — un nouvel utilisateur lance une automatisation de démonstration avec le guide intégré (< 5 min)", async ({ page }) => {
  const t0 = Date.now();
  await login(page, "alice");
  await expect(page.getByRole("heading", { name: "Démarrer en 5 minutes" })).toBeVisible();
  await page.getByRole("link", { name: "Ouvrez le catalogue" }).click();
  await expect(page.getByRole("heading", { name: "Catalogue d'automatisations" })).toBeVisible();
  await page.getByRole("link", { name: "Statistiques de texte" }).click();
  await page.getByRole("link", { name: "Lancer", exact: true }).click();
  await page.getByLabel(/Texte/).fill("premier essai");
  await page.getByRole("button", { name: "Lancer l'automatisation" }).click();
  await expect(page.getByText("L'exécution s'est terminée avec succès.")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("mots", { exact: true })).toBeVisible();
  const seconds = (Date.now() - t0) / 1000;
  console.log(`SCENARIO_S1_SECONDS=${seconds.toFixed(1)}`);
  expect(seconds).toBeLessThan(300);
});

test("S2 — une erreur d'exécution dit ce qui a échoué, ce qui a déjà réussi et la suite possible", async ({ page }) => {
  await login(page, "alice");
  // step 1 succeeds; step 2 receives a text above the action's declared maximum (10 000 chars) -> deterministic INPUT_INVALID
  const res = await page.evaluate(async () => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    const h = { "Content-Type": "application/json", "X-CSRFToken": csrf };
    const slug = `scenario-erreur-${Date.now() % 100000}`;
    const base = "/api/v1/workspaces/demo";
    await fetch(`${base}/workflows/`, { method: "POST", headers: h, body: JSON.stringify({ slug, name: slug }) });
    const graph = {
      inputs: { type: "object", required: ["texte"], properties: { texte: { type: "string", title: "Texte" } } }, outputs: {},
      edges: [{ from: "pause", to: "analyse" }],
      nodes: [
        { id: "pause", type: "action", name: "Courte pause", action: "slow-wait", inputs: { seconds: 1 } },
        { id: "analyse", type: "action", name: "Analyser le texte", action: "text-stats", inputs: { text: "{{ inputs.texte }}" } },
      ],
    };
    await fetch(`${base}/workflows/${slug}/draft/`, { method: "PUT", headers: h, body: JSON.stringify({ graph, revision: 1 }) });
    const pub = await fetch(`${base}/workflows/${slug}/publish/`, { method: "POST", headers: h, body: "{}" });
    const run = await (await fetch(`${base}/workflows/${slug}/runs/`, { method: "POST", headers: h, body: JSON.stringify({ environment: "test", inputs: { texte: "x".repeat(10001) } }) })).json();
    return { pub: pub.status, id: run.id };
  });
  expect(res.pub).toBe(201);
  await page.goto(`/runs/${res.id}`);
  await expect(page.getByText("L'exécution a échoué.")).toBeVisible({ timeout: 90_000 });
  const summary = page.getByRole("region", { name: "En résumé" });
  await expect(summary).toContainText("1 étape(s) réussie(s) : Courte pause"); // what already succeeded
  await expect(summary).toContainText("Échec : Analyser le texte"); // what failed
  await expect(summary).toContainText("Que faire ensuite"); // what to do next
});

test("S3 — un approbateur comprend et traite une demande depuis un téléphone", async ({ browser }) => {
  const t0 = Date.now();
  const marker = `S3-${Date.now()}`;
  const requester = await (await browser.newContext()).newPage();
  await login(requester, "alice");
  await goNav(requester, "Workflows");
  await requester.getByRole("row", { name: /Analyse avec validation/ }).getByRole("link", { name: /Lancer/ }).click();
  await requester.getByLabel(/Texte à analyser/).fill(`un deux trois quatre ${marker}`);
  await requester.getByRole("button", { name: "Lancer l'automatisation" }).click();
  await expect(requester.getByText("Une approbation est attendue").first()).toBeVisible({ timeout: 30_000 });
  const phone = await (await browser.newContext({ viewport: { width: 375, height: 667 }, hasTouch: true })).newPage();
  await login(phone, "claire");
  await goNav(phone, "Approbations");
  const card = phone.locator(".approval-row", { hasText: marker });
  await expect(card).toContainText("Valider l'analyse");
  await expect(card).toContainText("demandé par alice");
  const noHScroll = await phone.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
  expect(noHScroll).toBe(true); // no horizontal page scroll at phone width
  await card.getByRole("button", { name: /Examiner/ }).click();
  await phone.getByRole("dialog").getByRole("button", { name: "Approuver", exact: true }).click();
  await expect(phone.locator(".approval-row", { hasText: marker })).toHaveCount(0, { timeout: 15_000 });
  console.log(`SCENARIO_S3_SECONDS=${((Date.now() - t0) / 1000).toFixed(1)}`);
});
