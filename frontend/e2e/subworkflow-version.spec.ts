import { expect, test, type Page } from "@playwright/test";
import { login } from "./helpers";

/**
 * Version épinglée d'un workflow appelé (étapes subworkflow, foreach, while). Régression : le menu annonçait « (v1.2.0) » — la dernière version de l'enfant —
 * alors que le brouillon et la version publiée du parent référençaient 1.1.0, et « Brouillon enregistré » s'affichait sans que le nouveau choix existe côté serveur.
 */
const uniq = () => `${Date.now()}`.slice(-7);

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
        body: body ? JSON.stringify(body) : undefined,
      });
      return { status: r.status, data: await r.json().catch(() => ({})) };
    },
    { method, path, body },
  );
}

/** Enfant publié trois fois (1.0.0, 1.1.0, 1.2.0) : la sortie `which` dit quelle version a répondu. */
async function publishChild(page: Page, slug: string) {
  await call(page, "POST", "/workflows/", { slug, name: `Enfant ${slug}` });
  let revision = 1;
  for (const v of ["1.0.0", "1.1.0", "1.2.0"]) {
    const graph = {
      inputs: { type: "object", required: ["text"], properties: { text: { type: "string" } } },
      nodes: [{ id: "a", type: "action", name: "Analyser", action: "text-stats", inputs: { text: "{{ inputs.text }}" } }],
      edges: [],
      outputs: { words: "{{ steps.a.output.words }}", which: `v${v}` },
    };
    const put = await call(page, "PUT", `/workflows/${slug}/draft/`, { graph, revision });
    expect(put.status).toBe(200);
    revision = put.data.revision;
    const pub = await call(page, "POST", `/workflows/${slug}/publish/`, { bump: "minor" });
    expect(pub.status).toBe(201);
    expect(pub.data.version).toBe(v);
  }
}

const FLOW = {
  subworkflow: (child: string, version: string) => ({ id: "c", type: "subworkflow", name: "Appel", workflow: child, version, inputs: { text: "a b" } }),
  foreach: (child: string, version: string) => ({
    id: "c",
    type: "foreach",
    name: "Appel",
    workflow: child,
    version,
    items: "inputs.items",
    item_input: "text",
    max_iterations: 3,
  }),
  while: (child: string, version: string) => ({
    id: "c",
    type: "while",
    name: "Appel",
    workflow: child,
    version,
    condition: "iteration >= 1",
    max_iterations: 2,
    inputs: { text: "a b" },
  }),
} as const;
const OUT = { subworkflow: "{{ steps.c.output.which }}", foreach: "{{ steps.c.output.results }}", while: "{{ steps.c.output.last.which }}" } as const;
const TAB = { subworkflow: "Workflow", foreach: "Boucle", while: "Boucle" } as const;

for (const kind of ["subworkflow", "foreach", "while"] as const) {
  test(`${kind} : choisir 1.2.0 est enregistré, survit au rechargement, est publié puis exécuté — et 1.1.0 n'est jamais remplacée d'office`, async ({
    page,
  }) => {
    await login(page);
    const child = `enf-${uniq()}`;
    const parent = `par-${kind}-${uniq()}`;
    await publishChild(page, child);
    await call(page, "POST", "/workflows/", { slug: parent, name: `Parent ${parent}` });
    const graph = {
      inputs: { type: "object", properties: { items: { type: "array" } } },
      nodes: [FLOW[kind](child, "1.1.0")],
      edges: [],
      outputs: { r: OUT[kind] },
    };
    const seeded = await call(page, "PUT", `/workflows/${parent}/draft/`, { graph, revision: 1 });
    expect(seeded.status).toBe(200);
    const before = (await call(page, "GET", `/workflows/${parent}/`)).data.draft_revision as number;

    // 1. l'éditeur affiche la valeur du graphe (1.1.0), pas la dernière version de l'enfant (1.2.0)
    await page.goto(`/workflows/${parent}/schema`);
    await page.locator(".flow-node").filter({ hasText: "Appel" }).first().click();
    await page.getByRole("tab", { name: TAB[kind], exact: true }).click();
    const select = page.getByLabel("Version du workflow appelé");
    await expect(select).toHaveValue("1.1.0");
    await expect(page.getByText(/La version 1\.2\.0, plus récente, n'est pas utilisée/)).toBeVisible();
    await page.waitForTimeout(2500); // ouvrir le panneau n'a rien modifié : aucune écriture automatique
    expect((await call(page, "GET", `/workflows/${parent}/`)).data.draft_revision).toBe(before);

    // 2. choisir 1.2.0 : la révision augmente et l'enregistrement est confirmé par le serveur
    const [saved] = await Promise.all([
      page.waitForResponse((r) => r.url().endsWith(`/workflows/${parent}/draft/`) && r.request().method() === "PUT", { timeout: 30_000 }),
      select.selectOption("1.2.0"),
    ]);
    expect(saved.status()).toBe(200);
    const body = await saved.json();
    expect(body.revision).toBe(before + 1);
    expect(body.graph.nodes[0].version).toBe("1.2.0");
    await expect(page.getByRole("status").filter({ hasText: "Brouillon enregistré" }).first()).toBeVisible({ timeout: 15_000 });

    // 3. rechargement complet : 1.2.0 reste choisie (interface), et l'API contient version: "1.2.0"
    await page.reload();
    await page.locator(".flow-node").filter({ hasText: "Appel" }).first().click();
    await page.getByRole("tab", { name: TAB[kind], exact: true }).click();
    await expect(page.getByLabel("Version du workflow appelé")).toHaveValue("1.2.0");
    const draft = (await call(page, "GET", `/workflows/${parent}/`)).data;
    expect(draft.draft_graph.nodes[0].version).toBe("1.2.0");
    expect(draft.draft_revision).toBe(before + 1);

    // 4. la publication suivante la conserve ; l'exécution appelle réellement la 1.2.0
    const pub = await call(page, "POST", `/workflows/${parent}/publish/`, { bump: "minor" });
    expect(pub.status).toBe(201);
    const run = await call(page, "POST", `/workflows/${parent}/runs/`, { environment: "test", inputs: { items: ["a b"] } });
    expect(run.status).toBeLessThan(300);
    await expect
      .poll(async () => (await call(page, "GET", `/workflow-runs/${run.data.id}/`)).data.status, { timeout: 90_000, intervals: [1000] })
      .toBe("succeeded");
    const done = (await call(page, "GET", `/workflow-runs/${run.data.id}/`)).data;
    const r = done.outputs.r;
    expect(kind === "foreach" ? r[0].which : r).toBe("v1.2.0");
  });
}

test("« Toujours la dernière » : enregistrée, conservée à la publication, et chaque exécution appelle la dernière version de l'enfant", async ({ page }) => {
  await login(page);
  const child = `enf-${uniq()}`;
  const parent = `par-latest-${uniq()}`;
  await publishChild(page, child);
  await call(page, "POST", "/workflows/", { slug: parent, name: `Parent ${parent}` });
  const graph = { inputs: { type: "object", properties: {} }, nodes: [FLOW.subworkflow(child, "1.1.0")], edges: [], outputs: { r: OUT.subworkflow } };
  expect((await call(page, "PUT", `/workflows/${parent}/draft/`, { graph, revision: 1 })).status).toBe(200);

  await page.goto(`/workflows/${parent}/schema`);
  await page.locator(".flow-node").filter({ hasText: "Appel" }).first().click();
  await page.getByRole("tab", { name: "Workflow", exact: true }).click();
  const [saved] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith(`/workflows/${parent}/draft/`) && r.request().method() === "PUT", { timeout: 30_000 }),
    page.getByLabel("Version du workflow appelé").selectOption("latest"),
  ]);
  expect((await saved.json()).graph.nodes[0].version).toBe("latest");
  await page.reload();
  await page.locator(".flow-node").filter({ hasText: "Appel" }).first().click();
  await page.getByRole("tab", { name: "Workflow", exact: true }).click();
  await expect(page.getByLabel("Version du workflow appelé")).toHaveValue("latest");

  const pub = await call(page, "POST", `/workflows/${parent}/publish/`, { bump: "minor" });
  expect(pub.status).toBe(201);
  const runOnce = async () => {
    const run = await call(page, "POST", `/workflows/${parent}/runs/`, { environment: "test", inputs: {} });
    expect(run.status).toBeLessThan(300);
    await expect
      .poll(async () => (await call(page, "GET", `/workflow-runs/${run.data.id}/`)).data.status, { timeout: 90_000, intervals: [1000] })
      .toBe("succeeded");
    return (await call(page, "GET", `/workflow-runs/${run.data.id}/`)).data.outputs.r as string;
  };
  expect(await runOnce()).toBe("v1.2.0");
  // l'enfant publie une 4e version : la même version publiée du parent l'appelle, sans republication
  const cur = (await call(page, "GET", `/workflows/${child}/`)).data;
  const next = { ...cur.draft_graph, outputs: { ...cur.draft_graph.outputs, which: "v1.3.0" } };
  expect((await call(page, "PUT", `/workflows/${child}/draft/`, { graph: next, revision: cur.draft_revision })).status).toBe(200);
  expect((await call(page, "POST", `/workflows/${child}/publish/`, { bump: "minor" })).data.version).toBe("1.3.0");
  expect(await runOnce()).toBe("v1.3.0");
});
