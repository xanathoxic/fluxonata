// Comparaison de l'interface RÉELLE (sans zoom, port 5173) avec la RÉFÉRENCE : la même application d'origine vue à 80 % de zoom du navigateur (port 5174).
// Référence : un navigateur à 80 % = mise en page sur 2400 px CSS de large, rendue à l'échelle 0,8 ; Playwright : viewport 2400×1250, deviceScaleFactor 0,8 (capture 1920×1000).
// Réelle : viewport 1920×1000, échelle 1. Les deux utilisent la même API et les mêmes données ; le DOM est le même, seul le CSS diffère.
// Pour chaque page : (1) écart pixel par pixel, (2) écart par élément (position, taille, taille de texte ; les mesures de la référence sont multipliées par 0,8).
// Usage : node scripts/ui-compare.mjs [--only motif] ; la référence se lance depuis une copie du commit d'origine : voir docs/UI_REFERENCE.md (ou : ORCH_WEB_PORT=5174 npx vite --port 5174).
import { chromium } from "../frontend/node_modules/playwright/index.mjs";
import { mkdirSync, writeFileSync } from "node:fs";

const REAL = process.env.UI_REAL ?? "http://127.0.0.1:5173";
const REF = process.env.UI_REF ?? "http://127.0.0.1:5174";
const ZOOM = 0.8;
const OUT = new URL("../.run/ui-compare/", import.meta.url).pathname;
mkdirSync(OUT, { recursive: true });
const only = process.argv.includes("--only") ? process.argv[process.argv.indexOf("--only") + 1] : "";

const STATIC = ["/", "/catalog", "/workflows", "/library/actions", "/library/forms", "/library/controls", "/connections", "/triggers", "/approvals", "/executions", "/ops", "/admin", "/account", "/forms"];

async function login(browser, base, ref) {
  const ctx = await browser.newContext(ref
    ? { viewport: { width: Math.round(1920 / ZOOM), height: Math.round(1000 / ZOOM) }, deviceScaleFactor: ZOOM, locale: "fr-FR" }
    : { viewport: { width: 1920, height: 1000 }, deviceScaleFactor: 1, locale: "fr-FR" });
  const page = await ctx.newPage();
  await page.goto(base + "/");
  await page.getByLabel("Identifiant").fill("admin");
  await page.getByLabel("Mot de passe").fill("demo-fluxonata");
  await page.getByRole("button", { name: "Se connecter" }).click();
  await page.waitForSelector("header.topbar", { timeout: 30000 });
  return page;
}

async function pagesToVisit(page) {
  const get = (p) => page.evaluate(async (p) => { const r = await fetch(`/api/v1/workspaces/demo${p}`); return r.ok ? r.json() : null; }, p);
  const out = [...STATIC];
  const cat = await get("/catalog/?limit=1"); const c = cat?.results?.[0]?.slug; if (c) out.push(`/catalog/${c}`);
  const wf = await get("/workflows/?limit=1&search=Analyse"); const w = wf?.results?.[0]?.slug; if (w) out.push(`/workflows/${w}/schema`, `/workflows/${w}/versions`);
  const fm = await get("/form-library/?limit=1"); const f = fm?.results?.[0]?.slug; if (f) out.push(`/forms/${f}`);
  const ct = await get("/control-library/?limit=1"); const k = ct?.results?.[0]?.slug; if (k) out.push(`/library/controls/${k}`);
  return out;
}

const MEASURE = () => {
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
  const els = [...document.body.querySelectorAll("*")].filter((e) => !["SCRIPT", "STYLE", "PATH", "CIRCLE", "RECT", "LINE", "G", "POLYLINE", "POLYGON", "DEFS", "TITLE"].includes(e.tagName.toUpperCase()) && vis(e));
  return els.map((e) => {
    const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    const own = [...e.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join(" ").slice(0, 24);
    const cls = typeof e.className === "string" ? e.className.trim().split(/\s+/).slice(0, 2).join(".") : "";
    return { k: `${e.tagName.toLowerCase()}${cls ? "." + cls : ""}`, t: own, x: r.x + scrollX, y: r.y + scrollY, w: r.width, h: r.height, fs: parseFloat(s.fontSize), text: own.length > 0 };
  });
};

async function settle(page, url) {
  await page.goto(url, { waitUntil: "load" });
  await page.waitForSelector("header.topbar", { timeout: 30000 });
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.evaluate(() => document.fonts.ready);
  const font = await page.evaluate(() => document.fonts.check('12px "Plus Jakarta Sans Variable"') && [...document.fonts].some((f) => f.family.includes("Plus Jakarta") && f.status === "loaded"));
  if (!font) throw new Error(`police Plus Jakarta Sans non chargée sur ${url} : la comparaison serait faussée (largeurs de texte)`);
  await page.waitForTimeout(900);
  await page.evaluate(() => window.scrollTo(0, 0));
}

function compare(ref, real) {
  const n = Math.min(ref.length, real.length);
  let paired = 0, good = 0, fsRatios = [];
  const bad = [], first = [];
  for (let i = 0; i < n; i++) {
    const a = ref[i], b = real[i];
    if (a.k !== b.k || a.t !== b.t) continue;
    paired++;
    const d = { x: Math.abs(a.x * ZOOM - b.x), y: Math.abs(a.y * ZOOM - b.y), w: Math.abs(a.w * ZOOM - b.w), h: Math.abs(a.h * ZOOM - b.h) };
    const fsr = a.text ? b.fs / (a.fs * ZOOM) : 1; if (a.text) fsRatios.push(fsr);
    const ok = d.x <= 2 && d.y <= 2 && d.w <= 2 && d.h <= 2 && Math.abs(fsr - 1) <= 0.04;
    if (ok) good++; else { if (first.length < 12) first.push({ k: a.k, t: a.t, dx: +(b.x - a.x * ZOOM).toFixed(1), dy: +(b.y - a.y * ZOOM).toFixed(1), dw: +(b.w - a.w * ZOOM).toFixed(1), dh: +(b.h - a.h * ZOOM).toFixed(1), ref: [a.x * ZOOM, a.y * ZOOM, a.w * ZOOM, a.h * ZOOM].map((v) => +v.toFixed(1)) }); bad.push({ k: a.k, t: a.t, dx: +d.x.toFixed(1), dy: +d.y.toFixed(1), dw: +d.w.toFixed(1), dh: +d.h.toFixed(1), fs_ref: +(a.fs * ZOOM).toFixed(1), fs_real: b.fs, score: d.x + d.y + d.w + d.h + Math.abs(fsr - 1) * 100 }); }
  }
  fsRatios.sort((p, q) => p - q);
  return { ref_n: ref.length, real_n: real.length, paired, good, pct: paired ? +(100 * good / paired).toFixed(1) : 0, fs_median: fsRatios.length ? +fsRatios[Math.floor(fsRatios.length / 2)].toFixed(3) : null, docBad: bad.slice(0, 200), first, worst: bad.sort((p, q) => q.score - p.score).slice(0, 6) };
}

async function pixelDiff(browser, a, b, name) {
  const p = await browser.newPage();
  const r = await p.evaluate(async ({ a, b }) => {
    const load = (s) => new Promise((res) => { const i = new Image(); i.onload = () => res(i); i.src = "data:image/png;base64," + s; });
    const [ia, ib] = await Promise.all([load(a), load(b)]);
    const w = Math.min(ia.width, ib.width), h = Math.min(ia.height, ib.height);
    const cv = (i) => { const c = document.createElement("canvas"); c.width = w; c.height = h; const x = c.getContext("2d"); x.drawImage(i, 0, 0); return x.getImageData(0, 0, w, h); };
    const da = cv(ia), db = cv(ib), out = document.createElement("canvas"); out.width = w; out.height = h; const ox = out.getContext("2d"); const od = ox.createImageData(w, h);
    let diff = 0;
    for (let i = 0; i < da.data.length; i += 4) {
      const d = Math.max(Math.abs(da.data[i] - db.data[i]), Math.abs(da.data[i + 1] - db.data[i + 1]), Math.abs(da.data[i + 2] - db.data[i + 2]));
      const bad = d > 40; if (bad) diff++;
      od.data[i] = bad ? 255 : da.data[i] * 0.25 + 190; od.data[i + 1] = bad ? 40 : da.data[i + 1] * 0.25 + 190; od.data[i + 2] = bad ? 40 : da.data[i + 2] * 0.25 + 190; od.data[i + 3] = 255;
    }
    ox.putImageData(od, 0, 0);
    return { pct: +(100 * diff / (w * h)).toFixed(2), png: out.toDataURL("image/png").split(",")[1], size: [ia.width, ia.height, ib.width, ib.height] };
  }, { a: a.toString("base64"), b: b.toString("base64") });
  await p.close();
  writeFileSync(OUT + name + ".diff.png", Buffer.from(r.png, "base64"));
  return { pct: r.pct, size: r.size };
}

const browser = await chromium.launch();
const refPage = await login(browser, REF, true);
const realPage = await login(browser, REAL, false);
const list = (await pagesToVisit(realPage)).filter((p) => !only || p.includes(only));
const report = [];
for (const path of list) {
  const name = (path === "/" ? "accueil" : path.replace(/^\//, "").replace(/[^a-z0-9]+/gi, "_")).slice(0, 60);
  try {
    await settle(refPage, REF + path); await settle(realPage, REAL + path);
    const [mr, mb] = [await refPage.evaluate(MEASURE), await realPage.evaluate(MEASURE)];
    const sr = await refPage.screenshot(); const sb = await realPage.screenshot();
    writeFileSync(OUT + name + ".ref.png", sr); writeFileSync(OUT + name + ".real.png", sb);
    const px = await pixelDiff(browser, sr, sb, name);
    const c = compare(mr, mb);
    report.push({ path, pixel_diff_pct: px.pct, ...c });
    console.log(`${path.padEnd(42)} éléments conformes ${String(c.pct).padStart(5)} %  (${c.good}/${c.paired}, DOM ${c.ref_n}/${c.real_n})  texte ×${c.fs_median}  pixels différents ${px.pct} %`);
  } catch (e) { console.log(`${path.padEnd(42)} ÉCHEC : ${String(e.message).split("\n")[0]}`); report.push({ path, error: String(e.message).split("\n")[0] }); }
}
writeFileSync(OUT + "rapport.json", JSON.stringify(report, null, 1));
const ok = report.filter((r) => !r.error);
if (ok.length) console.log(`\nMoyenne : éléments conformes ${(ok.reduce((s, r) => s + r.pct, 0) / ok.length).toFixed(1)} %, pixels différents ${(ok.reduce((s, r) => s + r.pixel_diff_pct, 0) / ok.length).toFixed(2)} % — détail : ${OUT}rapport.json`);
await browser.close();
