import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, goNav, login, publishViaApi } from "./helpers";

const uniq = () => `${Date.now()}`.slice(-7);

test("Cache : un workflow écrit une valeur, la page « Caches » la montre, la révèle et l'invalide", async ({ page }) => {
  const id = uniq();
  const slug = `e2e-cache-${id}`;
  const key = `e2e-cache-${id}`;
  await login(page);
  const status = await publishViaApi(page, slug, {
    inputs: { type: "object", properties: {}, required: [] },
    outputs: {},
    edges: [],
    nodes: [{ id: "w", type: "cache", name: "Écrire", operation: "write", key, value: { serveurs: ["a", "b"], total: 2 }, ttl_seconds: 3600 }],
  });
  expect(status).toBe(201);
  const runId = await page.evaluate(async (s) => {
    const csrf =
      document.cookie
        .split("; ")
        .find((c) => c.startsWith("csrftoken="))
        ?.split("=")[1] ?? "";
    const r = await fetch(`/api/v1/workspaces/demo/workflows/${s}/runs/`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify({ environment: "test", inputs: {} }),
    });
    return (await r.json()).id as string;
  }, slug);
  await expect
    .poll(() => page.evaluate(async (r) => (await (await fetch(`/api/v1/workspaces/demo/workflow-runs/${r}/`)).json()).status, runId), { timeout: 30_000 })
    .toBe("succeeded");

  await goNav(page, "Bibliothèques");
  await page.getByRole("tab", { name: "Caches" }).click();
  await page.getByLabel("Rechercher une clé").fill(key);
  const row = page.getByRole("table", { name: "Entrées du cache" }).getByRole("row").filter({ hasText: key });
  await expect(row).toContainText("Test");
  await expectNoA11yViolations(page);

  await row.getByRole("button", { name: `Voir la valeur de ${key}` }).click();
  const dlg = page.getByRole("dialog", { name: `Cache « ${key} »` });
  await expect(dlg.getByLabel("Valeur du cache")).toContainText('"serveurs"');
  await expectNoA11yViolations(page);
  await page.keyboard.press("Escape");

  await row.getByRole("button", { name: `Invalider ${key} (Test)` }).click();
  await expectNoA11yViolations(page);
  await page.getByRole("dialog").getByRole("button", { name: "Invalider", exact: true }).click();
  await expect(row).toHaveCount(0);

  await page.evaluate(async (s) => {
    const csrf =
      document.cookie
        .split("; ")
        .find((c) => c.startsWith("csrftoken="))
        ?.split("=")[1] ?? "";
    await fetch(`/api/v1/workspaces/demo/workflows/${s}/delete/`, { method: "DELETE", headers: { "X-CSRFToken": csrf } });
  }, slug);
});

test("Cache : le réglage « Publier le résultat » alimente le cache à la fin d'une exécution réussie", async ({ page }) => {
  const id = uniq();
  const slug = `e2e-cache-pub-${id}`;
  const key = `e2e-pub-${id}`;
  await login(page);
  const status = await publishViaApi(page, slug, {
    inputs: { type: "object", properties: {}, required: [] },
    outputs: { n: "{{ steps.t.output.n }}" },
    edges: [],
    nodes: [{ id: "t", type: "transform", name: "Calcul", op: "set", fields: { n: 42 } }],
  });
  expect(status).toBe(201);
  await page.goto(`/workflows/${slug}/summary`);
  const sections = page.getByRole("navigation", { name: "Sections du workflow" });
  await expect(sections.getByRole("link", { name: "Informations" })).toBeVisible();
  await expect(sections.getByRole("link", { name: "Cache", exact: true })).toHaveCount(0); // onglet visible seulement une fois la publication activée
  await page.getByRole("tab", { name: "Cache" }).click();
  await page.getByLabel("Publier le résultat dans le cache (clé)").fill(key);
  await page.getByLabel("Durée de vie du résultat (secondes, facultatif)").fill("600");
  await page.getByRole("button", { name: "Enregistrer", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Enregistré." })).toBeVisible();
  await sections.getByRole("link", { name: "Cache", exact: true }).click();
  await expect(page.getByText(`Cache de ce workflow : ${key}`)).toBeVisible();
  await expect(page.getByText(/Rien dans le cache pour l'instant/)).toBeVisible();
  await expectNoA11yViolations(page);

  const value = await page.evaluate(
    async ({ s, k }) => {
      const csrf =
        document.cookie
          .split("; ")
          .find((c) => c.startsWith("csrftoken="))
          ?.split("=")[1] ?? "";
      const h = { "Content-Type": "application/json", "X-CSRFToken": csrf };
      const r = await fetch(`/api/v1/workspaces/demo/workflows/${s}/runs/`, {
        method: "POST",
        headers: h,
        body: JSON.stringify({ environment: "test", inputs: {} }),
      });
      const run = (await r.json()).id as string;
      for (let i = 0; i < 40; i++) {
        const st = (await (await fetch(`/api/v1/workspaces/demo/workflow-runs/${run}/`)).json()).status;
        if (st === "succeeded") break;
        await new Promise((ok) => setTimeout(ok, 500));
      }
      const list = (await (await fetch(`/api/v1/workspaces/demo/caches/?q=${k}`)).json()).results as { id: number }[];
      const detail = list.length ? await (await fetch(`/api/v1/workspaces/demo/caches/${list[0]?.id}/`)).json() : null;
      if (list.length) await fetch(`/api/v1/workspaces/demo/caches/${list[0]?.id}/`, { method: "DELETE", headers: { "X-CSRFToken": csrf } });
      await fetch(`/api/v1/workspaces/demo/workflows/${s}/delete/`, { method: "DELETE", headers: { "X-CSRFToken": csrf } });
      return detail?.value;
    },
    { s: slug, k: key },
  );
  expect(value).toEqual({ n: 42 });
});

test("Cache : une valeur absente est reconstruite par le workflow producteur, une seule fois pour deux lecteurs", async ({ page }) => {
  const id = uniq();
  const producer = `e2e-prod-${id}`;
  const reader = `e2e-lect-${id}`;
  const key = `e2e-rebuild-${id}`;
  await login(page);
  const empty = { inputs: { type: "object", properties: {}, required: [] }, edges: [] };
  expect(
    await publishViaApi(page, producer, {
      ...empty,
      outputs: { n: "{{ steps.t.output.n }}" },
      nodes: [{ id: "t", type: "transform", name: "Calcul", op: "set", fields: { n: 7 } }],
    }),
  ).toBe(201);
  expect(
    await publishViaApi(page, reader, {
      ...empty,
      outputs: { v: "{{ steps.r.output.value }}", rebuilt: "{{ steps.r.output.rebuilt }}" },
      nodes: [{ id: "r", type: "cache", name: "Lire", operation: "read", key, on_missing: "rebuild" }],
    }),
  ).toBe(201);
  const result = await page.evaluate(
    async ({ p, r, k }) => {
      const csrf =
        document.cookie
          .split("; ")
          .find((c) => c.startsWith("csrftoken="))
          ?.split("=")[1] ?? "";
      const h = { "Content-Type": "application/json", "X-CSRFToken": csrf };
      const base = "/api/v1/workspaces/demo";
      await fetch(`${base}/workflows/${p}/meta/`, { method: "PATCH", headers: h, body: JSON.stringify({ cache_key: k, cache_ttl_seconds: 600 }) });
      const start = async () =>
        (await (await fetch(`${base}/workflows/${r}/runs/`, { method: "POST", headers: h, body: JSON.stringify({ environment: "test", inputs: {} }) })).json())
          .id as string;
      const ids = [await start(), await start()];
      const runs: { status: string; outputs: { v?: { n: number }; rebuilt?: boolean } }[] = [];
      for (const run of ids) {
        for (let i = 0; i < 60; i++) {
          const d = await (await fetch(`${base}/workflow-runs/${run}/`)).json();
          if (d.status !== "running") {
            runs.push(d);
            break;
          }
          await new Promise((ok) => setTimeout(ok, 500));
        }
      }
      let producerRuns = 0;
      for (const run of ids) producerRuns += ((await (await fetch(`${base}/workflow-runs/${run}/children/`)).json()) as unknown[]).length;
      const entries = (await (await fetch(`${base}/caches/?q=${k}`)).json()).results as { id: number }[];
      for (const e of entries) await fetch(`${base}/caches/${e.id}/`, { method: "DELETE", headers: { "X-CSRFToken": csrf } });
      for (const s of [p, r]) await fetch(`${base}/workflows/${s}/delete/`, { method: "DELETE", headers: { "X-CSRFToken": csrf } });
      return { runs, producerRuns, entries: entries.length };
    },
    { p: producer, r: reader, k: key },
  );
  expect(result.runs.map((x) => x.status)).toEqual(["succeeded", "succeeded"]);
  expect(result.runs.map((x) => x.outputs.v)).toEqual([{ n: 7 }, { n: 7 }]);
  expect(result.producerRuns).toBe(1);
  expect(result.entries).toBe(1);
});
