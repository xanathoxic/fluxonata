import { expect, test, type Page } from "@playwright/test";
import { login } from "./helpers";

/** Runner imposé à un workflow : choisi dans les réglages, enregistré, relu après rechargement, et extension aux sous-workflows. */
const uniq = () => `${Date.now()}`.slice(-7);

async function call(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(
    async ({ method, path, body }) => {
      const csrf =
        document.cookie
          .split("; ")
          .find((c) => c.startsWith("csrftoken="))
          ?.split("=")[1] ?? "";
      const r = await fetch(`/api/v1${path}`, {
        method,
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: body ? JSON.stringify(body) : undefined,
      });
      return { status: r.status, data: await r.json().catch(() => ({})) };
    },
    { method, path, body },
  );
}

test("workflow : imposer un runner et l'étendre aux sous-workflows, relu après rechargement ; un runner inconnu est refusé", async ({ page }) => {
  await login(page, "admin");
  const id = uniq();
  const runner = `rw${id}`;
  const slug = `wr-${id}`;
  const made = await call(page, "POST", "/platform/runners/", {
    name: runner,
    scope: "shared",
    runtimes: ["python", "shell"],
    tags: ["linux"],
    max_concurrent: 1,
  });
  expect(made.status).toBe(201);
  expect((await call(page, "POST", "/workspaces/demo/workflows/", { slug, name: `Runner ${id}` })).status).toBe(201);

  await page.goto(`/workflows/${slug}/summary`);
  const select = page.getByLabel("Runner imposé");
  await expect(select.getByRole("option", { name: new RegExp(`^${runner} — partagé`) })).toHaveCount(1);
  await expect(page.getByLabel("Imposer ce runner aux sous-workflows")).toBeDisabled();
  await select.selectOption({ label: (await select.getByRole("option", { name: new RegExp(`^${runner}`) }).textContent()) ?? "" });
  await page.getByLabel("Imposer ce runner aux sous-workflows").check();
  await page.getByRole("button", { name: "Enregistrer", exact: true }).click();
  await expect(page.getByText("Enregistré.")).toBeVisible({ timeout: 15_000 });

  // relu côté serveur, puis après rechargement complet de la page
  const saved = (await call(page, "GET", `/workspaces/demo/workflows/${slug}/`)).data;
  expect(saved.runner_name).toBe(runner);
  expect(saved.runner_force_children).toBe(true);
  await page.reload();
  await expect(page.getByLabel("Runner imposé")).toHaveValue(made.data.id);
  await expect(page.getByLabel("Imposer ce runner aux sous-workflows")).toBeChecked();

  // un runner qui n'existe pas est refusé par le serveur ; retirer le runner décoche l'extension
  expect((await call(page, "PATCH", `/workspaces/demo/workflows/${slug}/meta/`, { runner: "00000000-0000-0000-0000-000000000000" })).status).toBe(400);
  await page.getByLabel("Runner imposé").selectOption("");
  await expect(page.getByLabel("Imposer ce runner aux sous-workflows")).not.toBeChecked();
  await page.getByRole("button", { name: "Enregistrer", exact: true }).click();
  await expect(page.getByText("Enregistré.")).toBeVisible({ timeout: 15_000 });
  expect((await call(page, "GET", `/workspaces/demo/workflows/${slug}/`)).data.runner).toBeNull();

  // nettoyage : le workflow (jamais exécuté) puis le runner
  expect((await call(page, "DELETE", `/workspaces/demo/workflows/${slug}/delete/`)).status).toBe(204);
  expect((await call(page, "DELETE", `/platform/runners/${made.data.id}/`)).status).toBe(204);
});
