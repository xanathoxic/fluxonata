import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, login, publishViaApi } from "./helpers";

// Un assistant (MCP) peut produire des workflows de plus de 100 étapes : l'éditeur doit rester utilisable à cette taille (ADR 0025).
const BLOCKS = 21; // 6 étapes par bloc = 126

function bigGraph() {
  const nodes: unknown[] = [];
  const edges: { from: string; to: string; label?: string }[] = [];
  let prev = "";
  for (let i = 0; i < BLOCKS; i++) {
    const p = `b${String(i).padStart(2, "0")}`;
    const script = (id: string, name: string, code: string, out: string) => ({ id, type: "script", name, language: "python", script: code, bindings: {}, outputs: [{ name: out, type: "integer" }] });
    nodes.push(
      script(`${p}_prep`, `Préparer ${i}`, `v${i} = ${i}\n`, `v${i}`),
      { id: `${p}_test`, type: "condition", name: `Pair ? ${i}`, expr: `inputs.n % 2 == ${i % 2}` },
      script(`${p}_oui`, `Oui ${i}`, "r = 1\n", "r"),
      script(`${p}_non`, `Non ${i}`, "r = 2\n", "r"),
      { id: `${p}_join`, type: "join", name: `Reprise ${i}` },
      script(`${p}_fin`, `Conclure ${i}`, "z = 1\n", "z"),
    );
    edges.push({ from: `${p}_prep`, to: `${p}_test` }, { from: `${p}_test`, to: `${p}_oui`, label: "true" }, { from: `${p}_test`, to: `${p}_non`, label: "false" }, { from: `${p}_oui`, to: `${p}_join` }, { from: `${p}_non`, to: `${p}_join` }, { from: `${p}_join`, to: `${p}_fin` });
    if (prev) edges.push({ from: prev, to: `${p}_prep` });
    prev = `${p}_fin`;
  }
  return { inputs: { type: "object", required: ["n"], properties: { n: { type: "integer" } } }, nodes, edges, outputs: {} };
}

test("un workflow de plus de 100 étapes s'ouvre, reste réactif et accessible", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error" && !/favicon|Failed to load resource/.test(m.text())) errors.push(m.text()); });
  const slug = `gros-${Date.now()}`.slice(0, 20);
  await login(page);
  expect(await publishViaApi(page, slug, bigGraph())).toBe(201);

  const t0 = Date.now();
  await page.goto(`/workflows/${slug}/schema`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible({ timeout: 30_000 });
  const opened = Date.now() - t0;
  console.log(`éditeur : première étape visible en ${opened} ms`);
  expect(opened).toBeLessThan(15_000);

  // la vue s'ajuste à tout le graphe : la 1re étape est coupée au bord, on clique une étape franchement visible
  expect(await page.locator(".react-flow__node").count()).toBeGreaterThanOrEqual(126);
  // l'éditeur répond : sélectionner une étape ouvre son inspecteur
  const boxes = await page.locator(".react-flow__node").evaluateAll((els) => els.map((e) => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; }));
  const pick = boxes.find((b) => b.x > 560 && b.x + b.w < 1230 && b.y > 300 && b.y + b.h < 680 && b.w > 3);
  expect(pick, "au moins une étape entièrement dans la fenêtre").toBeTruthy();
  await page.mouse.click(pick!.x + pick!.w / 2, pick!.y + pick!.h / 2); // les étapes sont minuscules à cette échelle : clic par coordonnées
  await expect(page.getByRole("complementary").or(page.getByRole("region", { name: /étape|inspecteur/i })).first()).toBeVisible({ timeout: 10_000 });
  await expectNoA11yViolations(page);
  expect(errors, errors.join("\n")).toEqual([]);

  // la liste des workflows contient toujours celui-ci (un gros graphe ne dégrade pas le reste de l'application)
  await page.goto("/workflows");
  await expect(page.getByRole("link", { name: `Test ${slug}` }).first()).toBeVisible({ timeout: 20_000 });
});
