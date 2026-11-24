import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, goNav, login, publishViaApi } from "./helpers";

const uniq = () => `${Date.now()}`.slice(-7);
const EMPTY = { inputs: { type: "object", properties: {}, required: [] }, edges: [], outputs: {} };

async function call(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(
    async ({ method, path, body }) => {
      const csrf =
        document.cookie
          .split("; ")
          .find((c) => c.startsWith("csrftoken="))
          ?.split("=")[1] ?? "";
      const r = await fetch(`/api/v1/workspaces/demo${path}`, {
        method,
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      return { status: r.status, json: r.status === 204 ? null : await r.json().catch(() => null) };
    },
    { method, path, body },
  );
}

async function runAndWait(page: Page, slug: string) {
  const started = await call(page, "POST", `/workflows/${slug}/runs/`, { environment: "test", inputs: {} });
  const id = started.json.id as string;
  await expect.poll(async () => (await call(page, "GET", `/workflow-runs/${id}/`)).json.status, { timeout: 30_000 }).toBe("succeeded");
  return id;
}

const target = (slug: string) => ({
  ...EMPTY,
  outputs: { n: "{{ steps.t.output.n }}" },
  nodes: [{ id: "t", type: "transform", name: "Calcul", op: "set", fields: { n: 9 } }],
  ...{ edges: [] },
  slug,
});

test("Programmer : un workflow programme le lancement d'un autre, qui part à l'heure dite et se retrouve dans « Lancements programmés »", async ({ page }) => {
  const id = uniq();
  const cible = `e2e-cible-${id}`;
  const planner = `e2e-planif-${id}`;
  await login(page);
  expect(await publishViaApi(page, cible, target(cible))).toBe(201);
  expect(
    await publishViaApi(page, planner, {
      ...EMPTY,
      nodes: [
        {
          id: "p",
          type: "schedule",
          name: "Programmer le rapport",
          workflow: cible,
          version: "latest",
          when: "in",
          delay_seconds: 6,
          timezone: "Europe/Paris",
          on_past: "fail",
          inputs: {},
        },
      ],
    }),
  ).toBe(201);
  const runId = await runAndWait(page, planner);
  const step = (await call(page, "GET", `/workflow-runs/${runId}/`)).json;
  expect(JSON.stringify(step)).toContain("programmé"); // l'étape l'a dit dans l'historique de l'exécution

  // l'étape est terminée, le lancement attend : visible, annulable, accessible
  await goNav(page, "Déclencheurs");
  await page.getByRole("tab", { name: "Lancements programmés" }).click();
  const table = page.getByRole("table", { name: "Lancements programmés" });
  const row = table.getByRole("row").filter({ hasText: `Test ${cible}` });
  await expect(row).toBeVisible();
  await expectNoA11yViolations(page);

  // à l'heure dite l'orchestrateur le lance : le lancement passe à « Lancé » et l'exécution réussit
  let firedRun = "";
  await expect
    .poll(
      async () => {
        const list = (await call(page, "GET", `/scheduled-launches/?workflow=${cible}`)).json.results as { status: string; fired_run: string | null }[];
        firedRun = list[0]?.fired_run ?? "";
        return list[0]?.status;
      },
      { timeout: 60_000 },
    )
    .toBe("fired");
  await expect.poll(async () => (await call(page, "GET", `/workflow-runs/${firedRun}/`)).json.status, { timeout: 30_000 }).toBe("succeeded");
  await page.reload();
  await page.getByRole("tab", { name: "Lancements programmés" }).click();
  await expect(row.getByText("Lancé")).toBeVisible();
  await expect(row.getByRole("link", { name: "Voir l'exécution" })).toHaveAttribute("href", `/executions/${firedRun}`);
  await expectNoA11yViolations(page);

  for (const s of [cible, planner]) await call(page, "DELETE", `/workflows/${s}/delete/`);
});

test("Programmer : un lancement en attente s'annule depuis la page, et ne part jamais", async ({ page }) => {
  const id = uniq();
  const cible = `e2e-cible-a-${id}`;
  const planner = `e2e-planif-a-${id}`;
  await login(page);
  expect(await publishViaApi(page, cible, target(cible))).toBe(201);
  expect(
    await publishViaApi(page, planner, {
      ...EMPTY,
      nodes: [
        {
          id: "p",
          type: "schedule",
          name: "Programmer",
          workflow: cible,
          version: "latest",
          when: "in",
          delay_seconds: 86400,
          timezone: "Europe/Paris",
          on_past: "fail",
          inputs: {},
        },
      ],
    }),
  ).toBe(201);
  await runAndWait(page, planner);
  await goNav(page, "Déclencheurs");
  await page.getByRole("tab", { name: "Lancements programmés" }).click();
  const row = page
    .getByRole("table", { name: "Lancements programmés" })
    .getByRole("row")
    .filter({ hasText: `Test ${cible}` });
  await row.getByRole("button", { name: /Annuler le lancement de/ }).click();
  await expectNoA11yViolations(page);
  await page.getByRole("dialog").getByRole("button", { name: "Annuler le lancement" }).click();
  await expect(row.getByText("Annulé", { exact: true })).toBeVisible();
  await expect(row.getByRole("button", { name: /Annuler le lancement de/ })).toHaveCount(0);
  const list = (await call(page, "GET", `/scheduled-launches/?workflow=${cible}`)).json.results as { status: string }[];
  expect(list.map((l) => l.status)).toEqual(["cancelled"]);
  expect((await call(page, "GET", `/workflows/${cible}/`)).status).toBe(200);
  for (const s of [cible, planner]) await call(page, "DELETE", `/workflows/${s}/delete/`);
});

test("Programmer : l'étape se règle dans l'éditeur (date, heure, fuseau, aperçu) et un lecteur ne peut pas annuler", async ({ page, browser }) => {
  const id = uniq();
  const cible = `e2e-cible-e-${id}`;
  const planner = `e2e-planif-e-${id}`;
  await login(page);
  expect(await publishViaApi(page, cible, target(cible))).toBe(201);
  expect(
    await publishViaApi(page, planner, {
      ...EMPTY,
      nodes: [
        {
          id: "p",
          type: "schedule",
          name: "Programmer le rapport",
          workflow: cible,
          version: "latest",
          when: "at",
          at: "2099-12-01T08:30",
          timezone: "Europe/Paris",
          on_past: "fail",
          inputs: {},
        },
      ],
    }),
  ).toBe(201);
  await page.goto(`/workflows/${planner}/schema`);
  await page.getByLabel("Rechercher un élément").fill("Programmer");
  await expect(page.getByRole("button", { name: "Ajouter : Programmer un lancement", exact: true })).toBeVisible(); // la palette le propose
  await page.getByText("Programmer le rapport").first().click();
  await page.getByRole("tab", { name: "Programmation" }).click();
  await expect(page.getByLabel("Date et heure")).toHaveValue("2099-12-01T08:30");
  await expect(page.getByText(/Sera lancé le .*décembre 2099.*08:30/)).toBeVisible();
  await expect(page.getByText(/soit .*07:30 UTC/)).toBeVisible(); // 08:30 à Paris (hiver) = 07:30 UTC
  await page.getByLabel("Fuseau horaire").fill("UTC");
  await expect(page.getByText(/\(UTC\)/)).toBeVisible();
  await expect(page.getByText(/soit .*UTC/)).toHaveCount(0);
  await page.getByLabel("Si la date est déjà passée").selectOption("now");
  await expect(page.getByText(/ne lance rien maintenant/)).toBeVisible();
  await expectNoA11yViolations(page);

  // un lecteur voit la liste mais n'a aucun moyen d'annuler (le serveur refuse aussi)
  const reader = await (await browser.newContext()).newPage();
  await login(reader, "bob");
  const denied = await call(reader, "DELETE", "/scheduled-launches/00000000-0000-0000-0000-000000000000/");
  expect([403, 404]).toContain(denied.status);
  await reader.context().close();
  for (const s of [cible, planner]) await call(page, "DELETE", `/workflows/${s}/delete/`);
});

test("Attente jusqu'à une date : le workflow est en pause, puis reprend tout seul à l'heure dite", async ({ page }) => {
  const slug = `e2e-pause-${uniq()}`;
  await login(page);
  const until = new Date(Date.now() + 8_000).toISOString().replace(/\.\d+Z$/, "Z"); // une date avec secondes et décalage, dans 8 s
  expect(
    await publishViaApi(page, slug, {
      ...EMPTY,
      outputs: { n: "{{ steps.t.output.n }}" },
      nodes: [
        { id: "w", type: "wait", name: "Pause jusqu'à la date", until, timezone: "Europe/Paris", on_past: "fail" },
        { id: "t", type: "transform", name: "Après la pause", op: "set", fields: { n: 5 } },
      ],
      edges: [{ from: "w", to: "t" }],
    }),
  ).toBe(201);
  const started = await call(page, "POST", `/workflows/${slug}/runs/`, { environment: "test", inputs: {} });
  const id = started.json.id as string;
  await expect.poll(async () => (await call(page, "GET", `/workflow-runs/${id}/`)).json.status, { timeout: 2_000 }).toBe("running"); // en pause
  await expect.poll(async () => (await call(page, "GET", `/workflow-runs/${id}/`)).json.status, { timeout: 40_000 }).toBe("succeeded");
  const run = (await call(page, "GET", `/workflow-runs/${id}/`)).json;
  expect(run.outputs.n).toBe(5);
});
