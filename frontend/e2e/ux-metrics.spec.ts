import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { login, publishViaApi } from "./helpers";

/** Mesures de base de l'ergonomie (audit UX) : elles sont ENREGISTRÉES, pas encore bloquantes. Chaque lot de la mise en œuvre rendra un seuil bloquant
 *  (voir docs/UX_PROMPT_IMPLEMENTATION.md) : (a) textes de moins de 12 px, (b) cibles de moins de 24 px, (c) saut vertical de la barre d'onglets
 *  d'un onglet à l'autre, (d) largeur du canevas quand une étape est sélectionnée. Le résultat est écrit dans test-results/ux-metrics.json. */

interface Sample { what: string; size: string }
interface Screen { screen: string; smallText: number; smallTargets: number; textSamples: Sample[]; targetSamples: Sample[]; tabBarY?: number | null }
const rows: Screen[] = [];
const jumps: { group: string; positions: Record<string, number | null>; jump: number }[] = [];
const canvas: { viewport: number; canvasWidth: number | null; ratio: number | null; inspectorWidth: number | null; paletteWidth: number | null }[] = [];

async function call(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ({ method, path, body }) => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
    const r = await fetch(`/api/v1/workspaces/demo${path}`, { method, headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: body ? JSON.stringify(body) : undefined });
    return { status: r.status, data: await r.json().catch(() => ({})) };
  }, { method, path, body });
}

/** (a) visible elements with their own text smaller than 12 px, (b) visible interactive elements smaller than 24 px in either direction. */
async function measure(page: Page, screen: string, tabBar?: string): Promise<Screen> {
  await page.waitForLoadState("networkidle").catch(() => undefined);
  const m = await page.evaluate((tabSelector) => {
    const visible = (el: Element) => {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      return r.width > 1 && r.height > 1 && cs.visibility !== "hidden" && cs.display !== "none" && cs.opacity !== "0";
    };
    const label = (el: Element) => `${el.tagName.toLowerCase()}${el.className && typeof el.className === "string" ? "." + el.className.split(" ")[0] : ""} « ${(el.getAttribute("aria-label") ?? el.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 40)} »`;
    const text: { what: string; size: string }[] = [];
    let smallText = 0;
    for (const el of Array.from(document.body.querySelectorAll("*"))) {
      if (!["SCRIPT", "STYLE", "SVG", "PATH"].includes(el.tagName.toUpperCase()) && Array.from(el.childNodes).some((n) => n.nodeType === 3 && (n.textContent ?? "").trim() !== "") && visible(el)) {
        const px = parseFloat(getComputedStyle(el).fontSize);
        if (px < 12) { smallText++; if (text.length < 6) text.push({ what: label(el), size: `${px}px` }); }
      }
    }
    const targets: { what: string; size: string }[] = [];
    let smallTargets = 0;
    for (const el of Array.from(document.querySelectorAll("a[href], button, select, textarea, input:not([type=hidden]), [role=tab], [role=button], [role=menuitem]"))) {
      if (!visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 24 || r.height < 24) { smallTargets++; if (targets.length < 6) targets.push({ what: label(el), size: `${Math.round(r.width)}×${Math.round(r.height)}` }); }
    }
    const bar = tabSelector ? document.querySelector(tabSelector) : null;
    return { smallText, smallTargets, text, targets, tabBarY: bar ? Math.round(bar.getBoundingClientRect().top + window.scrollY) : null };
  }, tabBar ?? "");
  const row: Screen = { screen, smallText: m.smallText, smallTargets: m.smallTargets, textSamples: m.text, targetSamples: m.targets, tabBarY: m.tabBarY };
  rows.push(row);
  return row;
}

test.describe.configure({ mode: "serial" });
test.afterAll(() => {
  const worst = (k: "smallText" | "smallTargets") => rows.filter((r) => r[k] > 0).sort((a, b) => b[k] - a[k]).slice(0, 8).map((r) => `${r.screen}: ${r[k]}`);
  mkdirSync("test-results", { recursive: true });
  writeFileSync("test-results/ux-metrics.json", JSON.stringify({ date: new Date().toISOString(), screens: rows, tabBarJumps: jumps, canvas }, null, 2));
  console.log(`\nUX-METRICS (${rows.length} écrans)\n  textes < 12 px — pire : ${worst("smallText").join(" | ")}\n  cibles < 24 px — pire : ${worst("smallTargets").join(" | ")}`);
  for (const j of jumps) console.log(`  saut de la barre d'onglets — ${j.group} : ${j.jump} px (${JSON.stringify(j.positions)})`);
  for (const c of canvas) console.log(`  canevas à ${c.viewport} px : ${c.canvasWidth} px (${c.ratio !== null ? Math.round(c.ratio * 100) : "?"} %) · inspecteur ${c.inspectorWidth ?? "absent"} px · palette ${c.paletteWidth ?? "absente"} px`);
});

test("écrans clés : accueil, catalogue, workflows", async ({ page }) => {
  await login(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  for (const [screen, url] of [["accueil", "/home"], ["catalogue", "/catalog"], ["workflows", "/workflows"]] as const) {
    await page.goto(url);
    await measure(page, screen);
  }
});

test("les 9 onglets d'un workflow : textes, cibles, position de la barre d'onglets", async ({ page }) => {
  await login(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  const slug = `ux-${Date.now().toString().slice(-6)}`;
  await publishViaApi(page, slug, {
    inputs: { type: "object", properties: { sujet: { type: "string", title: "Sujet" } }, required: [] }, outputs: {}, edges: [{ from: "a", to: "b" }],
    nodes: [{ id: "a", type: "wait", name: "Pause", seconds: 1 }, { id: "b", type: "wait", name: "Seconde pause", seconds: 1 }],
  });
  const tabs = ["summary", "variables", "io", "schema", "follow", "form", "versions", "audit", "runs"];
  const positions: Record<string, number | null> = {};
  for (const t of tabs) {
    await page.goto(`/workflows/${slug}/${t}`);
    const r = await measure(page, `workflow · ${t}`, "nav.wf-tabs");
    positions[t] = r.tabBarY ?? null;
  }
  const ys = Object.values(positions).filter((y): y is number => y !== null);
  jumps.push({ group: "workflow (9 onglets)", positions, jump: ys.length ? Math.max(...ys) - Math.min(...ys) : 0 });
  expect(jumps[jumps.length - 1]?.jump, "la barre d\'onglets ne doit pas bouger d\'un onglet à l\'autre").toBe(0);

  // (d) largeur du canevas avec une étape sélectionnée
  for (const width of [1440, 1366]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`/workflows/${slug}/schema`);
    const step = page.locator(".react-flow__node", { hasText: "Seconde pause" });
    await step.waitFor();
    await step.click();
    await page.locator(".node-panel").waitFor({ timeout: 5000 }).catch(() => undefined);
    await page.waitForTimeout(600);
    const w = await page.locator(".canvas").first().evaluate((e) => e.getBoundingClientRect().width).catch(() => null);
    const side = async (sel: string) => ((await page.locator(sel).count()) ? page.locator(sel).first().evaluate((e) => Math.round(e.getBoundingClientRect().width)) : null);
    canvas.push({ viewport: width, canvasWidth: w === null ? null : Math.round(w), ratio: w === null ? null : w / width, inspectorWidth: await side(".node-panel"), paletteWidth: await side(".palette") });
    // seuils du lot 4 : canevas ≥ 55 % à 1440 px, ≥ 450 px à 1366 px, avec une étape sélectionnée
    if (width === 1440) expect((w ?? 0) / width).toBeGreaterThanOrEqual(0.55);
    if (width === 1366) expect(w ?? 0).toBeGreaterThanOrEqual(450);
  }
});

test("formulaire : 4 onglets et 4 modes de l'éditeur", async ({ page }) => {
  await login(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  const slug = `ux-form-${Date.now().toString().slice(-6)}`;
  const def = { version: 1, sections: [{ id: "s1", title: "Demande", fields: [{ key: "sujet", label: "Sujet", type: "text", required: true }, { key: "motif", label: "Motif", type: "select", options: [{ value: "a", label: "A" }] }] }] };
  await call(page, "POST", "/form-library/", { slug, name: `Mesure ${slug}`, definition: def });
  await call(page, "POST", `/form-library/${slug}/publish/`, {});
  const positions: Record<string, number | null> = {};
  for (const t of ["design", "summary", "versions", "usage"]) {
    await page.goto(`/library/forms/${slug}/${t}`);
    const r = await measure(page, `formulaire · onglet ${t}`, "[role=tablist][aria-label=Formulaire]");
    positions[t] = r.tabBarY ?? null;
  }
  const ys = Object.values(positions).filter((y): y is number => y !== null);
  jumps.push({ group: "formulaire (4 onglets)", positions, jump: ys.length ? Math.max(...ys) - Math.min(...ys) : 0 });
  expect(jumps[jumps.length - 1]?.jump, "la barre d\'onglets ne doit pas bouger d\'un onglet à l\'autre").toBe(0);
  await page.goto(`/library/forms/${slug}/design`);
  for (const mode of ["Éditeur", "Apparence", "Aperçu"]) {
    await page.getByRole("tablist", { name: "Vue de l'éditeur" }).getByRole("tab", { name: mode }).click();
    await measure(page, `formulaire · mode ${mode}`);
  }
});

test("contrôle : 4 onglets (et sections de la conception)", async ({ page }) => {
  await login(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  const slug = `ux-ctl-${Date.now().toString().slice(-6)}`;
  await call(page, "POST", "/control-library/", { slug, name: `Mesure ${slug}`, spec: { version: 1, mode: "group", parts: [{ key: "nom", label: "Nom", type: "text" }] } });
  const positions: Record<string, number | null> = {};
  for (const t of ["design", "preview", "versions", "usage"]) {
    await page.goto(`/library/controls/${slug}/${t}`);
    const r = await measure(page, `contrôle · onglet ${t}`, "[role=tablist][aria-label=Contrôle]");
    positions[t] = r.tabBarY ?? null;
  }
  const ys = Object.values(positions).filter((y): y is number => y !== null);
  jumps.push({ group: "contrôle (4 onglets)", positions, jump: ys.length ? Math.max(...ys) - Math.min(...ys) : 0 });
  expect(jumps[jumps.length - 1]?.jump, "la barre d\'onglets ne doit pas bouger d\'un onglet à l\'autre").toBe(0);
  await page.goto(`/library/controls/${slug}/design`);
  for (const name of ["Modèle d'une option", "Champs", "Documentation"]) {
    const btn = page.getByRole("navigation", { name: "Sections du contrôle" }).getByRole("button", { name });
    if (await btn.count()) { await btn.click(); await measure(page, `contrôle · section ${name}`); }
  }
});
