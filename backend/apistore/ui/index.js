// Interface de l'extension « API store » (contrat : docs/EXTENSIONS.md).
// Tout le texte passe par textContent : aucune valeur du serveur n'est jamais interprétée comme du HTML.
// Les droits sont décidés par le serveur ; l'interface ne fait que présenter ses réponses (et ses refus).

const STATUS = {
  draft: "Brouillon", published: "Publié", retired: "Retiré",
  pending: "En attente", active: "Active", suspended: "Suspendue", revoked: "Révoquée", rejected: "Refusée",
};
const OUTCOME = {
  accepted: "Accepté", replayed: "Rejoué", quota: "Quota dépassé", governance: "Refusé par la gouvernance",
  unauthorized: "Clé refusée", forbidden: "Interdit", invalid: "Invalide", error: "Erreur",
};

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}


const STYLE = `
.fx-store { display: grid; gap: 18px; width: 100%; max-width: 1700px; }
.fx-store h2 { margin: 0; font-size: var(--fs-xl, 22px); }
.fx-store h3 { margin: 0; font-size: var(--fs-md, 15px); }
.fx-store .fx-sub { color: var(--muted); margin: 2px 0 0; font-size: var(--fs-sm, 13px); }
.fx-store .fx-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); }
.fx-store .fx-tabs button { border: 0; border-bottom: 3px solid transparent; border-radius: 8px 8px 0 0; background: none; color: var(--muted); padding: 10px 16px; font-weight: 600; margin-bottom: -1px; }
.fx-store .fx-tabs button:hover:not([aria-selected=true]) { background: var(--accent-soft); color: var(--fg); }
.fx-store .fx-tabs button[aria-selected=true] { color: var(--accent); border-bottom-color: var(--accent); background: none; }
.fx-store .fx-tabs.sub { border-bottom: 0; gap: 8px; }
.fx-store .fx-tabs.sub button { border: 1px solid var(--border-mid); border-radius: 999px; padding: 6px 14px; margin: 0; }
.fx-store .fx-tabs.sub button[aria-selected=true] { background: var(--accent-soft); border-color: var(--accent); }
.fx-store .fx-tabs .fx-count.neutral { background: #e2e8f0; color: #334155; }
.fx-store .fx-tabs .fx-count { margin-left: 6px; background: var(--accent); color: var(--accent-fg); border-radius: 999px; padding: 0 7px; font-size: var(--fs-xs, 12px); }
.fx-store .fx-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 16px; align-items: start; }
.fx-store .fx-grid.wide { grid-template-columns: repeat(auto-fit, minmax(min(100%, 640px), 1fr)); }
.fx-store .fx-cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr)); gap: 24px; align-items: start; }
.fx-store .fx-cols > div { display: grid; gap: 12px; }
.fx-store [hidden] { display: none !important; }
.fx-store .fx-picks { border: 1px solid var(--border-strong); border-radius: 8px; padding: 8px 10px; margin: 0; display: grid; gap: 8px; background: var(--surface); }
.fx-store .fx-picks-list { display: grid; gap: 4px; max-height: 190px; overflow: auto; align-content: start; }
.fx-store .fx-picks legend { font-size: var(--fs-sm, 13px); font-weight: 600; color: var(--muted); padding: 0 4px; }
.fx-store .fx-pick { display: flex; align-items: center; gap: 8px; font-weight: 400; color: var(--fg); font-size: var(--fs-sm, 13px); }
.fx-store .fx-pick input { width: 16px; height: 16px; padding: 0; margin: 0; flex: none; accent-color: var(--accent); }
.fx-store .fx-pick small { color: var(--muted); }
.fx-store .fx-wide { flex: 1 1 100%; }
.fx-store .fx-label { display: block; font-size: var(--fs-sm, 13px); color: var(--muted); margin-bottom: 6px; }
.fx-store .fx-subscribe { display: grid; gap: 12px; padding-top: 14px; border-top: 1px solid var(--border); }
.fx-store .fx-plans-pick { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; }
.fx-store .fx-plan { display: flex; gap: 10px; align-items: flex-start; border: 1px solid var(--border-strong); border-radius: 10px; padding: 12px; cursor: pointer; background: var(--surface); }
.fx-store .fx-plan:has(input:checked) { border-color: var(--accent); background: var(--accent-soft); box-shadow: 0 0 0 1px var(--accent); }
.fx-store .fx-plan input { width: 16px; height: 16px; padding: 0; margin: 3px 0 0; flex: none; accent-color: var(--accent); }
.fx-store .fx-plan-body { display: grid; gap: 4px; font-size: var(--fs-sm, 13px); justify-items: start; }
.fx-store .fx-plan-body strong { font-size: var(--fs-md, 15px); }
.fx-store .fx-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius, 12px); box-shadow: var(--shadow); padding: 18px 20px; display: grid; gap: 12px; }
.fx-store .fx-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }
.fx-store .fx-desc { margin: 0; color: var(--muted); }
.fx-store .fx-chip { border-radius: 999px; padding: 2px 11px; font-size: var(--fs-xs, 12px); font-weight: 600; white-space: nowrap; background: var(--info-soft); color: var(--info); }
.fx-store .fx-chip.ok { background: var(--ok-soft); color: var(--ok); }
.fx-store .fx-chip.bad { background: var(--err-soft); color: var(--err); }
.fx-store .fx-chip.warn { background: var(--warn-bg); color: #92400e; }
.fx-store .fx-chip.neutral { background: #eef1f5; color: #334155; }
.fx-store .fx-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.fx-store .fx-plans { width: 100%; border-collapse: collapse; box-shadow: none; border-radius: 0; background: none; font-size: var(--fs-sm, 13px); }
.fx-store .fx-plans th, .fx-store .fx-plans td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); background: none; }
.fx-store .fx-form { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; padding-top: 12px; border-top: 1px solid var(--border); }
.fx-store .fx-field { display: grid; gap: 4px; flex: 1 1 150px; font-size: var(--fs-sm, 13px); font-weight: 600; color: var(--muted); }
.fx-store .fx-field input, .fx-store .fx-field select { font-weight: 400; color: var(--fg); width: 100%; box-sizing: border-box; }
.fx-store .fx-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.fx-store .fx-actions .danger { color: var(--err); border-color: var(--err); }
.fx-store .fx-issue { display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end; }
.fx-store .fx-issue .fx-field { flex: 0 1 220px; }
.fx-store .fx-issue input[type=number] { width: 90px; }
.fx-store .fx-keys { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.fx-store .fx-keys li { display: flex; justify-content: space-between; align-items: center; gap: 10px; background: var(--bg); border-radius: 8px; padding: 6px 10px; font-size: var(--fs-sm, 13px); }
.fx-store .fx-empty { text-align: center; color: var(--muted); padding: 36px 16px; background: var(--surface); border: 1px dashed var(--border-mid); border-radius: var(--radius, 12px); }
.fx-store .fx-banner { border-radius: 10px; padding: 12px 16px; background: var(--ok-soft); color: var(--ok); font-weight: 600; display: grid; gap: 8px; }
.fx-store .fx-banner.error { background: var(--err-soft); color: var(--err); }
.fx-store .fx-secret { display: block; background: var(--surface); color: var(--fg); border: 1px solid var(--border-strong); border-radius: 8px; padding: 10px 12px; font-family: ui-monospace, monospace; user-select: all; word-break: break-all; font-weight: 400; }
.fx-store .fx-usage { font-size: var(--fs-sm, 13px); display: grid; gap: 6px; }
.fx-store .fx-usage ul { margin: 0; padding-left: 18px; color: var(--muted); }
.fx-store h4 { margin: 0; font-size: var(--fs-sm, 13px); text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
.fx-store .fx-split { display: grid; grid-template-columns: minmax(280px, 340px) 1fr; gap: 18px; align-items: start; }
.fx-store .fx-side { display: grid; gap: 10px; position: sticky; top: 8px; max-height: calc(100vh - 24px); grid-template-rows: auto auto auto auto minmax(0, 1fr); }
.fx-store .fx-side .fx-sub { margin: 0; }
.fx-store .fx-rows { display: grid; gap: 6px; overflow: auto; align-content: start; padding: 2px; }
.fx-store .fx-row { display: grid; gap: 4px; text-align: left; justify-items: start; border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; background: var(--surface); }
.fx-store .fx-row .fx-desc { font-weight: 400; font-size: var(--fs-sm, 13px); }
.fx-store .fx-row-top { display: flex; justify-content: space-between; gap: 8px; width: 100%; align-items: center; }
.fx-store .fx-row[aria-current=true] { border-color: var(--accent); background: var(--accent-soft); box-shadow: 0 0 0 1px var(--accent); }
.fx-store .fx-main { min-width: 0; }
@media (max-width: 900px) { .fx-store .fx-split { grid-template-columns: 1fr; } .fx-store .fx-side { position: static; max-height: none; } .fx-store .fx-rows { max-height: 260px; } }
.fx-store .fx-section { display: grid; gap: 12px; }
.fx-store .fx-code { margin: 0; padding: 12px 14px; background: var(--bg); border: 1px solid var(--border-strong); border-radius: 8px; overflow: auto; font-size: var(--fs-sm, 13px); max-height: 420px; }
.fx-store .fx-code code { font-family: ui-monospace, monospace; white-space: pre; }
.fx-store .fx-ep-line { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.fx-store .fx-ep-path { word-break: break-all; font-size: var(--fs-md, 15px); }
.fx-store details summary { cursor: pointer; font-weight: 600; margin-bottom: 8px; }
`;

function addStyle(root) {
  if (root.ownerDocument.getElementById("fx-store-style")) return;
  const st = root.ownerDocument.createElement("style");
  st.id = "fx-store-style";
  st.textContent = STYLE;
  root.ownerDocument.head.append(st);
}

const field = (label, control) => h("label", { class: "fx-field" }, label, control);
const chip = (text, tone) => h("span", { class: `fx-chip ${tone || ""}` }, text);
const TONE = { published: "ok", active: "ok", pending: "warn", draft: "neutral", retired: "neutral", suspended: "warn", revoked: "bad", rejected: "bad" };
const statusChip = (st) => chip(STATUS[st] || st, TONE[st]);
const empty = (text) => h("div", { class: "fx-empty" }, text);
const limit = (n, unit) => (n == null ? "illimité" : `${n} / ${unit}`);
const when = (iso) => (iso ? new Date(iso).toLocaleString("fr-FR") : "—");

export default function mount(el, host) {
  const base = `/plugins/apistore/manage/${encodeURIComponent(host.workspace.slug)}`;
  const call = (path, init) => host.api(`${base}${path}`, init);
  let tab = "catalogue";
  let subTab = "active"; // « active » (en attente, actives, suspendues) ou « archive » (révoquées, refusées)
  let adminSel = null; // produit ouvert dans l'éditeur d'administration
  let adminQ = "";
  let adminStatus = "all";
  let creating = false;
  let confirmDelete = null; // id de la souscription dont la suppression attend une confirmation
  let notice = null; // { kind: "ok" | "error", text, secret? }
  let products = [];
  let subs = [];
  let rights = { administer: false, approve: false, run: false };
  let options = { workflows: [], accounts: [] };
  let usage = null; // { id, data }
  let docSel = null; // produit dont on lit la documentation
  let doc = null; // documentation générée du produit (réponse du serveur)
  let docEp = null; // endpoint ouvert
  let docLang = "curl";
  let disposed = false;

  // L'interface n'a que « Erreur » quand le serveur ne répond pas en JSON (page introuvable, panne) : on dit au moins de quoi il s'agit.
  function explain(e) {
    const msg = e && e.message;
    if (msg && msg !== "Erreur") return msg;
    const status = e && e.status;
    if (status === 404) return "Page introuvable sur le serveur : il n'a peut-être pas été redémarré depuis la dernière mise à jour, ou l'élément a été supprimé ou n'est pas publié.";
    if (status === 401 || status === 403) return "Accès refusé : reconnectez-vous ou demandez le droit nécessaire.";
    if (status >= 500) return "Le serveur a rencontré une erreur : réessayez, puis consultez ses journaux.";
    return msg || "Opération refusée.";
  }
  const fail = (e) => { notice = { kind: "error", text: explain(e) }; };
  const isAdmin = () => rights.administer;

  async function load() {
    try {
      [products, subs, rights] = await Promise.all([call("/products/"), call("/subscriptions/"), call("/me/")]);
      if (rights.administer) options = await call("/options/");
    } catch (e) { fail(e); }
    draw();
  }

  async function act(fn, okText) {
    try { await fn(); if (okText) notice = { kind: "ok", text: okText }; }
    catch (e) { fail(e); }
    await load();
  }

  function banner() {
    if (!notice) return null;
    const box = h("div", { role: notice.kind === "error" ? "alert" : "status", class: `fx-banner ${notice.kind === "error" ? "error" : ""}` }, notice.text);
    if (notice.secret) {
      box.append(
        h("span", {}, "Copiez cette clé maintenant : elle ne sera plus jamais affichée."),
        h("code", { "data-testid": "api-key", class: "fx-secret" }, notice.secret),
      );
    }
    return box;
  }

  const rate = (pl) => {
    if (pl.per_minute == null && pl.per_day == null) return "Appels illimités";
    return [pl.per_minute != null && `${pl.per_minute} appels / minute`, pl.per_day != null && `${pl.per_day} appels / jour`].filter(Boolean).join(" · ");
  };
  const ENV = { dev: "Développement", test: "Recette", prod: "Production" };

  function subscribeForm(p) {
    // Un plan = une tuile à choisir (bouton radio) : débit, environnements et mode d'approbation lisibles d'un coup d'œil.
    const name = `plan-${p.slug}`;
    const tiles = p.plans.map((pl, i) => h("label", { class: "fx-plan" },
      h("input", { type: "radio", name, value: pl.slug, checked: i === 0, "aria-label": `Plan ${pl.name} de ${p.name}` }),
      h("span", { class: "fx-plan-body" },
        h("strong", {}, pl.name),
        h("span", {}, rate(pl)),
        h("span", { class: "fx-desc" }, `Environnements : ${pl.environments.map((e) => ENV[e] || e).join(", ")}`),
        chip(pl.approval === "auto" ? "Accès immédiat" : "Validation par un approbateur", pl.approval === "auto" ? "ok" : "warn"))));
    const consumer = h("input", { "aria-label": `Application consommatrice pour ${p.name}`, placeholder: "Application consommatrice", maxlength: 120, required: true });
    return h("form", {
      class: "fx-subscribe",
      onsubmit: (ev) => {
        ev.preventDefault();
        const chosen = tiles.map((t) => t.querySelector("input")).find((i) => i.checked);
        act(() => call("/subscriptions/", { json: { product: p.slug, plan: chosen.value, consumer: consumer.value.trim() } }),
          "Demande enregistrée (elle peut attendre une approbation).");
      },
    },
      h("strong", {}, "1. Choisissez un plan"), h("div", { class: "fx-plans-pick" }, tiles),
      h("label", { class: "fx-field" }, h("strong", {}, "2. Nom de l'application qui appellera l'API"), consumer),
      h("button", { type: "submit", class: "primary" }, "Demander l'accès"));
  }

  function plansTable(plans) {
    if (!plans.length) return h("p", { class: "fx-desc" }, "Aucun plan.");
    return h("table", { class: "fx-plans" },
      h("thead", {}, h("tr", {}, ["Plan", "Débit", "Environnements", "Approbation"].map((t) => h("th", { scope: "col" }, t)))),
      h("tbody", {}, plans.map((pl) => h("tr", {},
        h("td", {}, pl.name),
        h("td", {}, rate(pl)),
        h("td", {}, pl.environments.map((e) => ENV[e] || e).join(", ")),
        h("td", {}, pl.approval === "auto" ? "Automatique" : "Manuelle")))));
  }

  const tags = (items, names) => h("div", { class: "fx-tags" }, items.length ? items.map((w) => chip((names && names[w]) || w, "neutral")) : [h("span", { class: "fx-desc" }, "Aucun workflow")]);

  function catalogue() {
    const shown = products.filter((p) => p.status === "published");
    if (!shown.length) return empty("Aucun produit publié pour l'instant.");
    return h("div", { class: "fx-grid" }, shown.map((p) => h("article", { class: "fx-card" },
      h("div", { class: "fx-head" }, h("h3", {}, p.name), statusChip(p.status)),
      h("p", { class: "fx-desc" }, p.description || "Aucune description."),
      h("div", {}, h("strong", { class: "fx-label" }, "Workflows que vous pourrez appeler"), tags(p.workflows, p.workflow_names)),
      h("div", { class: "fx-actions" }, h("button", { type: "button", "aria-label": `Documentation de ${p.name}`, onclick: () => openDocs(p.slug) }, "Documentation de l'API")),
      p.plans.length ? subscribeForm(p) : h("p", { class: "fx-desc" }, "Aucun plan disponible."))));
  }

  function keyRow(s, k) {
    return h("li", {},
      h("span", {}, `${k.prefix}… · ${k.expires_at ? `expire le ${when(k.expires_at)}` : "sans expiration"}`),
      k.revoked ? chip("Révoquée", "bad")
        : h("button", { type: "button", "aria-label": `Révoquer la clé ${k.prefix}`, onclick: () => act(() => call(`/subscriptions/${s.id}/keys/${k.id}/revoke/`, { json: {} }), "Clé révoquée.") }, "Révoquer"));
  }

  function usagePanel(s) {
    if (!usage || usage.id !== s.id) return null;
    const d = usage.data;
    const sum = Object.entries(d.last_24h).map(([o, n]) => `${OUTCOME[o] || o} : ${n}`).join(" · ") || "aucun appel";
    return h("div", { class: "fx-usage" }, h("strong", {}, `24 dernières heures — ${sum}`),
      h("ul", {}, d.recent.map((e) => h("li", {}, `${when(e.at)} — ${e.workflow} (${e.environment}) — ${OUTCOME[e.outcome] || e.outcome}`))));
  }

  const LIFETIMES = [["7", "7 jours"], ["30", "30 jours"], ["90", "90 jours"], ["180", "180 jours"], ["365", "1 an"], ["custom", "Durée personnalisée…"]];

  // Durée de vie choisie à l'émission ; « illimitée » n'est proposée qu'aux administrateurs (le serveur le refuse aux autres).
  function keyIssuer(s) {
    const life = h("select", { "aria-label": `Durée de validité de la clé pour ${s.product}` },
      [...LIFETIMES, ...(rights.administer ? [["unlimited", "Illimitée (sans expiration)"]] : [])].map(([v, l]) => h("option", { value: v, selected: v === "90" }, l)));
    const days = h("input", { type: "number", min: 1, max: 365, value: 30, hidden: true, "aria-label": "Nombre de jours de validité (1 à 365)" });
    life.addEventListener("change", () => { days.hidden = life.value !== "custom"; });
    return h("div", { class: "fx-issue" }, field("Validité de la clé", life), days,
      h("button", { type: "button", class: "primary", "aria-label": `Émettre une clé pour ${s.product}`, onclick: () => act(async () => {
        const body = life.value === "unlimited" ? { unlimited: true } : { days: Number(life.value === "custom" ? days.value : life.value) };
        const r = await call(`/subscriptions/${s.id}/keys/`, { json: body });
        notice = { kind: "ok", text: `Clé ${r.prefix}… émise (${r.expires_at ? `valable jusqu'au ${when(r.expires_at)}` : "sans expiration"}).`, secret: r.key };
      }) }, "Émettre une clé"));
  }

  function subCard(s, mine) {
    const live = s.status === "active";
    const status = (st, label, text, danger) => h("button", { type: "button", class: danger ? "danger" : "", onclick: () => act(() => call(`/subscriptions/${s.id}/status/`, { json: { status: st } }), text) }, label);
    return h("article", { class: "fx-card" },
      h("div", { class: "fx-head" },
        h("div", {}, h("h3", {}, `${s.product} · ${s.plan}`), h("p", { class: "fx-sub" }, `${s.consumer}${s.requested_by ? ` · demandé par ${s.requested_by}` : ""}`)),
        statusChip(s.status)),
      s.keys.length ? h("ul", { class: "fx-keys" }, s.keys.map((k) => keyRow(s, k))) : h("p", { class: "fx-desc" }, "Aucune clé."),
      h("div", { class: "fx-actions" },
        live && keyIssuer(s),
        h("button", { type: "button", "aria-label": `Voir l'usage de ${s.product}`, onclick: async () => {
          try { usage = { id: s.id, data: await call(`/subscriptions/${s.id}/usage/`) }; } catch (e) { fail(e); }
          draw();
        } }, "Usage"),
        !mine && s.status === "active" && status("suspended", "Suspendre", "Souscription suspendue."),
        !mine && s.status === "suspended" && status("active", "Réactiver", "Souscription réactivée."),
        !mine && !["revoked", "rejected"].includes(s.status) && status("revoked", "Révoquer", "Souscription révoquée (clés révoquées).", true)),
      usagePanel(s));
  }


  const ARCHIVED = ["revoked", "rejected"];

  function archiveCard(s, mine) {
    const confirming = confirmDelete === s.id;
    return h("article", { class: "fx-card" },
      h("div", { class: "fx-head" },
        h("div", {}, h("h3", {}, `${s.product} · ${s.plan}`), h("p", { class: "fx-sub" }, `${s.consumer}${s.requested_by ? ` · demandé par ${s.requested_by}` : ""}`)),
        statusChip(s.status)),
      s.status === "revoked" && h("p", { class: "fx-desc" }, "Les clés de cette souscription sont révoquées. La réactiver ne les rétablit pas : il faudra en émettre une nouvelle."),
      !mine && h("div", { class: "fx-actions" },
        s.status === "revoked" && h("button", { type: "button", class: "primary", "aria-label": `Réactiver la souscription ${s.id}`, onclick: () => act(() => call(`/subscriptions/${s.id}/status/`, { json: { status: "active" } }), "Souscription réactivée : émettez une nouvelle clé.") }, "Réactiver"),
        !confirming && h("button", { type: "button", class: "danger", "aria-label": `Supprimer la souscription ${s.id}`, onclick: () => { confirmDelete = s.id; draw(); } }, "Supprimer"),
        confirming && h("span", { class: "fx-desc" }, "Supprimer définitivement ? L'historique d'usage est conservé."),
        confirming && h("button", { type: "button", class: "danger", "aria-label": `Confirmer la suppression de ${s.id}`, onclick: () => act(async () => { confirmDelete = null; await call(`/subscriptions/${s.id}/`, { method: "DELETE" }); }, "Souscription supprimée.") }, "Confirmer"),
        confirming && h("button", { type: "button", onclick: () => { confirmDelete = null; draw(); } }, "Annuler")));
  }

  function mySubs() {
    const mine = !isAdmin();
    const active = subs.filter((s) => !ARCHIVED.includes(s.status));
    const archived = subs.filter((s) => ARCHIVED.includes(s.status));
    const pick = (id, label, n) => h("button", { type: "button", role: "tab", "aria-selected": String(subTab === id), onclick: () => { subTab = id; confirmDelete = null; notice = null; draw(); } },
      label, h("span", { class: "fx-count neutral" }, n));
    const shown = subTab === "archive" ? archived : active;
    const body = shown.length
      ? h("div", { class: "fx-grid" }, shown.map((s) => (subTab === "archive" ? archiveCard(s, mine) : subCard(s, mine))))
      : empty(subTab === "archive" ? "Aucune souscription archivée." : "Aucune souscription active.");
    return h("div", { class: "fx-section" },
      h("div", { class: "fx-tabs sub", role: "tablist", "aria-label": "Souscriptions actives ou archivées" }, pick("active", "Actives", active.length), pick("archive", "Archivées", archived.length)),
      body);
  }

  function approvals() {
    const pending = subs.filter((s) => s.status === "pending");
    if (!pending.length) return empty("Aucune demande en attente.");
    return h("div", { class: "fx-grid" }, pending.map((s) => h("article", { class: "fx-card" },
      h("div", { class: "fx-head" },
        h("div", {}, h("h3", {}, `${s.product} · ${s.plan}`), h("p", { class: "fx-sub" }, `${s.consumer} · demandé par ${s.requested_by || "?"} le ${when(s.created_at)}`)),
        statusChip(s.status)),
      h("div", { class: "fx-actions" },
        h("button", { type: "button", class: "primary", "aria-label": `Approuver la demande ${s.id}`, onclick: () => act(() => call(`/subscriptions/${s.id}/decision/`, { json: { approve: true } }), "Demande approuvée.") }, "Approuver"),
        h("button", { type: "button", class: "danger", "aria-label": `Refuser la demande ${s.id}`, onclick: () => act(() => call(`/subscriptions/${s.id}/decision/`, { json: { approve: false } }), "Demande refusée.") }, "Refuser")))));
  }

  // Administration : liste filtrable à gauche (tient 100 produits et plus), éditeur du produit choisi à droite.
  const matches = (p) => {
    const q = adminQ.trim().toLowerCase();
    const text = [p.name, p.slug, ...p.workflows, ...Object.values(p.workflow_names || {})].join(" ").toLowerCase();
    return (!q || text.includes(q)) && (adminStatus === "all" || p.status === adminStatus);
  };
  const subCount = (p, st) => subs.filter((s) => s.product === p.slug && (!st || s.status === st)).length;

  function admin() {
    const list = h("div", { class: "fx-rows", role: "group", "aria-label": "Produits" });
    const count = h("p", { class: "fx-sub", role: "status" });
    function renderList() {
      const shown = products.filter(matches);
      count.textContent = `${shown.length} produit${shown.length > 1 ? "s" : ""} sur ${products.length}`;
      list.replaceChildren(...(shown.length ? shown.map((p) => h("button", {
        type: "button", class: "fx-row", "aria-current": String(!creating && adminSel === p.slug),
        onclick: () => { adminSel = p.slug; creating = false; draw(); },
      },
        h("span", { class: "fx-row-top" }, h("strong", {}, p.name), statusChip(p.status)),
        h("span", { class: "fx-desc" }, `${p.workflows.length} workflow${p.workflows.length > 1 ? "s" : ""} · ${p.plans.length} plan${p.plans.length > 1 ? "s" : ""} · ${subCount(p, "active")} souscription${subCount(p, "active") > 1 ? "s" : ""}`),
        subCount(p, "pending") > 0 && chip(`${subCount(p, "pending")} demande${subCount(p, "pending") > 1 ? "s" : ""} en attente`, "warn"),
      )) : [h("p", { class: "fx-desc" }, "Aucun produit ne correspond.")]));
    }
    const search = h("input", { type: "search", "aria-label": "Rechercher un produit", placeholder: "Rechercher (nom, identifiant, workflow)…", value: adminQ,
      oninput: (ev) => { adminQ = ev.target.value; renderList(); } });
    const status = h("select", { "aria-label": "Filtrer par état", onchange: (ev) => { adminStatus = ev.target.value; renderList(); } },
      [["all", "Tous les états"], ["draft", "Brouillons"], ["published", "Publiés"], ["retired", "Retirés"]].map(([v, l]) => h("option", { value: v, selected: v === adminStatus }, l)));
    renderList();
    const chosen = products.find((p) => p.slug === adminSel) || (creating ? null : products[0]);
    if (chosen) adminSel = chosen.slug;
    return h("div", { class: "fx-split" },
      h("aside", { class: "fx-side" },
        h("button", { type: "button", class: "primary", onclick: () => { creating = true; draw(); } }, "+ Nouveau produit"),
        search, status, count, list),
      h("section", { class: "fx-main" }, creating || !chosen ? createForm() : adminProduct(chosen)));
  }

  function createForm() {
    const slug = h("input", { "aria-label": "Identifiant du produit", placeholder: "mon-produit", required: true, maxlength: 60 });
    const name = h("input", { "aria-label": "Nom du produit", placeholder: "Mon produit", required: true, maxlength: 120 });
    const desc = h("input", { "aria-label": "Description du produit", placeholder: "À quoi sert ce produit (facultatif)", maxlength: 500 });
    return h("form", { class: "fx-card fx-form", onsubmit: (ev) => {
      ev.preventDefault();
      const id = slug.value.trim();
      act(async () => { await call("/products/", { json: { slug: id, name: name.value.trim(), description: desc.value.trim() } }); adminSel = id; creating = false; }, "Produit créé (brouillon) : choisissez ses workflows, un compte et un plan, puis publiez.");
    } }, h("h3", { class: "fx-wide" }, "Nouveau produit"), field("Identifiant", slug), field("Nom", name), field("Description", desc),
    h("div", { class: "fx-actions fx-wide" }, h("button", { type: "submit", class: "primary" }, "Créer le produit"),
      products.length > 0 && h("button", { type: "button", onclick: () => { creating = false; draw(); } }, "Annuler")));
  }

  function adminProduct(p) {
    const q = encodeURIComponent(p.slug);
    const checks = options.workflows.map((w) => {
      const on = p.workflows.includes(w.slug);
      const box = h("input", { type: "checkbox", value: w.slug, checked: on, disabled: !w.published && !on });
      return h("label", { class: "fx-pick" }, box, w.name, h("small", {}, w.published ? w.slug : `${w.slug} · non publié`));
    });
    const known = new Set(options.workflows.map((w) => w.slug));
    const orphans = p.workflows.filter((x) => !known.has(x)); // lié mais plus listé : conservé tant qu'on ne le retire pas
    // Recherche : masque les lignes sans toucher aux cases (une sélection cachée reste envoyée) ; le compteur dit combien sont choisis.
    const counter = h("span", { class: "fx-desc", role: "status" });
    const empty = h("span", { class: "fx-desc", hidden: true }, "Aucun workflow ne correspond.");
    const refresh = () => {
      const q = finder.value.trim().toLowerCase();
      let visible = 0;
      checks.forEach((l, i) => {
        const w = options.workflows[i];
        const hit = !q || `${w.name} ${w.slug}`.toLowerCase().includes(q);
        l.hidden = !hit;
        if (hit) visible += 1;
      });
      empty.hidden = visible > 0 || checks.length === 0;
      const chosen = checks.filter((l) => l.querySelector("input").checked).length + orphans.length;
      counter.textContent = `${chosen} sélectionné${chosen > 1 ? "s" : ""} sur ${options.workflows.length}`;
    };
    const finder = h("input", { type: "search", "aria-label": `Rechercher un workflow de ${p.name}`, placeholder: "Rechercher un workflow…", oninput: refresh });
    checks.forEach((l) => l.querySelector("input").addEventListener("change", refresh));
    const picks = h("fieldset", { class: "fx-picks fx-wide" }, h("legend", {}, "Workflows exposés par ce produit"),
      finder,
      h("div", { class: "fx-picks-list" }, checks.length ? checks : h("span", { class: "fx-desc" }, "Aucun workflow dans cet espace."), empty,
        orphans.map((x) => h("span", { class: "fx-desc" }, `${x} (introuvable)`))),
      counter);
    refresh();
    const runAs = h("select", { "aria-label": `Compte de service de ${p.name}` },
      h("option", { value: "" }, "— choisir un compte —"),
      [...new Set([...options.accounts, ...(p.run_as ? [p.run_as] : [])])].map((n) => h("option", { value: n, selected: n === p.run_as }, n)));
    // La sélection affichée (cases, compte) est toujours celle qui part : publier ne perd jamais une sélection non enregistrée.
    const current = () => {
      const body = { workflows: [...checks.map((l) => l.querySelector("input")).filter((i) => i.checked).map((i) => i.value), ...orphans] };
      if (runAs.value) body.run_as = runAs.value;
      return body;
    };
    const save = h("form", { class: "fx-form", onsubmit: (ev) => {
      ev.preventDefault();
      act(() => call(`/products/${q}/`, { method: "PATCH", json: current() }), "Produit mis à jour.");
    } }, picks, field("Compte d'exécution (sous lequel les workflows tournent)", runAs), h("button", { type: "submit", class: "primary" }, "Enregistrer"));
    const setStatus = (status, label, primary) => h("button", { type: "button", class: primary ? "primary" : "", "aria-label": `${label} ${p.name}`, onclick: () => act(() => call(`/products/${q}/`, { method: "PATCH", json: status === "published" ? { ...current(), status } : { status } }), `Produit : ${STATUS[status].toLowerCase()}.`) }, label);
    const pslug = h("input", { "aria-label": `Identifiant du plan de ${p.name}`, placeholder: "standard", required: true });
    const envs = h("input", { "aria-label": `Environnements du plan de ${p.name}`, placeholder: "test, prod", required: true });
    const pm = h("input", { "aria-label": `Appels par minute de ${p.name}`, type: "number", min: 1, placeholder: "illimité" });
    const pd = h("input", { "aria-label": `Appels par jour de ${p.name}`, type: "number", min: 1, placeholder: "illimité" });
    const appr = h("select", { "aria-label": `Approbation du plan de ${p.name}` }, h("option", { value: "manual" }, "Manuelle"), h("option", { value: "auto" }, "Automatique"));
    const plan = h("form", { class: "fx-form", onsubmit: (ev) => {
      ev.preventDefault();
      act(() => call(`/products/${q}/plans/`, { json: {
        slug: pslug.value.trim(), name: pslug.value.trim(),
        per_minute: pm.value ? Number(pm.value) : null, per_day: pd.value ? Number(pd.value) : null,
        approval: appr.value, environments: envs.value.split(",").map((x) => x.trim()).filter(Boolean),
      } }), "Plan créé.");
    } }, field("Nouveau plan", pslug), field("Environnements", envs), field("Appels / minute", pm), field("Appels / jour", pd), field("Approbation", appr), h("button", { type: "submit" }, "Ajouter le plan"));
    const n = (st) => subCount(p, st);
    return h("article", { class: "fx-card" },
      h("div", { class: "fx-head" },
        h("div", {}, h("h3", {}, p.name), h("p", { class: "fx-sub" }, `${p.slug}${p.description ? ` · ${p.description}` : ""}`)),
        h("div", { class: "fx-actions" }, statusChip(p.status),
          p.status !== "published" && setStatus("published", "Publier", true),
          p.status === "published" && setStatus("retired", "Retirer", false))),
      h("div", { class: "fx-tags" }, chip(`${n("active")} active${n("active") > 1 ? "s" : ""}`, "ok"), n("pending") > 0 && chip(`${n("pending")} en attente`, "warn"), n("suspended") > 0 && chip(`${n("suspended")} suspendue${n("suspended") > 1 ? "s" : ""}`, "neutral")),
      h("div", { class: "fx-cols" },
        h("div", {}, h("h4", {}, "Contenu et exécution"), save),
        h("div", {}, h("h4", {}, "Plans"), plansTable(p.plans), plan)));
  }


  // ----- Documentation générée d'un produit : tout vient du serveur, qui la calcule depuis les workflows publiés et les plans.
  const origin = () => (typeof location !== "undefined" && location.origin && location.origin !== "null" ? location.origin : "https://hote.exemple");
  const fill = (text) => String(text == null ? "" : text).split("<hôte>").join(origin());
  const json = (v) => JSON.stringify(v, null, 2);
  // Les descriptions du serveur marquent le code entre accents graves : on le rend en <code>, sans jamais interpréter de HTML.
  const rich = (text) => String(text == null ? "" : text).split("`").map((part, i) => (i % 2 ? h("code", {}, part) : part));
  const code = (text, label) => h("pre", { class: "fx-code", tabindex: "0", "aria-label": label }, h("code", {}, text));

  async function openDocs(slug) {
    tab = "docs"; docSel = slug; doc = null; docEp = null; notice = null;
    draw();
    try { doc = await call(`/products/${encodeURIComponent(slug)}/docs/`); docEp = doc.endpoints.length ? doc.endpoints[0].id : null; }
    catch (e) { fail(e); }
    draw();
  }

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); notice = { kind: "ok", text: "Copié dans le presse-papiers." }; }
    catch { notice = { kind: "error", text: "Copie impossible : sélectionnez le texte à la main." }; }
    draw();
  }

  async function downloadSpec(slug) {
    try {
      const spec = await call(`/products/${encodeURIComponent(slug)}/docs/openapi/`);
      spec.servers = [{ url: origin() }];
      const link = h("a", { href: URL.createObjectURL(new Blob([json(spec)], { type: "application/json" })), download: `${slug}.openapi.json` });
      document.body.append(link); link.click(); link.remove();
    } catch (e) { fail(e); draw(); }
  }

  const table = (heads, rows, label) => h("table", { class: "fx-plans", "aria-label": label },
    h("thead", {}, h("tr", {}, heads.map((t) => h("th", { scope: "col" }, t)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c) => h("td", {}, c))))));

  function paramRows(ep) {
    return ep.parameters.map((q) => [
      h("code", {}, q.name), q.type,
      q.required ? chip("obligatoire", "warn") : "facultatif",
      [q.enum ? `Valeurs : ${q.enum.join(", ")}` : null, q.has_default ? `Défaut : ${JSON.stringify(q.default)}` : null, ...q.constraints].filter(Boolean).join(" · ") || "—",
      q.description || "—",
    ]);
  }

  function endpointCard(ep) {
    const langs = ["curl", "python", "javascript"];
    const LANG = { curl: "curl", python: "Python", javascript: "JavaScript" };
    return h("div", { class: "fx-section" },
      h("div", { class: "fx-ep-line" }, chip(ep.method, ep.method === "POST" ? "warn" : "ok"), h("code", { class: "fx-ep-path" }, fill("<hôte>" + ep.path))),
      ep.version ? h("p", { class: "fx-sub" }, `Workflow « ${ep.workflow} », version publiée ${ep.version}.`) : null,
      h("p", { class: "fx-desc" }, rich(ep.description)),
      h("h4", {}, "En-têtes"),
      table(["En-tête", "Obligatoire", "Rôle"], ep.headers.map((x) => [h("code", {}, x.name), x.required ? "oui" : "non", rich(x.description)]), "En-têtes"),
      ep.parameters.length ? [h("h4", {}, ep.input_schema ? "Entrées (champ « inputs » du corps)" : "Paramètres"),
        table(["Nom", "Type", "Obligatoire", "Contraintes", "Description"], paramRows(ep), "Entrées")] : null,
      ep.response_fields ? [h("h4", {}, "Champs de la réponse"), table(["Champ", "Sens"], ep.response_fields.map(([name, meaning]) => [h("code", {}, name), rich(meaning)]), "Champs de la réponse")] : null,
      ep.environments.length ? h("p", { class: "fx-desc" }, `Champ « environment » du corps : ${ep.environments.join(", ")} (selon votre plan).`) : null,
      ep.request_example ? [h("h4", {}, "Corps de la requête (exemple)"), code(json(ep.request_example), "Exemple de requête")] : null,
      h("h4", {}, "Réponses"),
      ep.responses.map((r) => h("div", {}, h("p", { class: "fx-desc" }, [chip(String(r.status), r.status < 300 ? "ok" : "bad"), rich(` ${r.description}`)]), code(json(r.example), `Réponse ${r.status}`))),
      ep.outputs.length ? h("p", { class: "fx-desc" }, `Sorties renvoyées dans « outputs » : ${ep.outputs.join(", ")}. Leur type n'est pas déclaré par le workflow.`) : null,
      h("h4", {}, "Exemples d'appel"),
      h("div", { class: "fx-tabs sub", role: "tablist", "aria-label": "Langage de l'exemple" }, langs.map((l) => h("button", { type: "button", role: "tab", "aria-selected": String(l === docLang), onclick: () => { docLang = l; draw(); } }, LANG[l]))),
      code(fill(ep.examples[docLang]), `Exemple ${LANG[docLang]}`),
      h("div", { class: "fx-actions" }, h("button", { type: "button", onclick: () => copy(fill(ep.examples[docLang])) }, `Copier l'exemple ${LANG[docLang]}`)),
      ep.input_schema ? h("details", {}, h("summary", {}, "Schéma JSON complet des entrées"), code(json(ep.input_schema), "Schéma JSON")) : null);
  }

  function docOverview(d) {
    return h("div", { class: "fx-section" },
      h("h4", {}, "Authentification"), h("p", { class: "fx-desc" }, d.auth.text), code(d.auth.header, "En-tête d'authentification"),
      h("p", { class: "fx-desc" }, `Adresse de base : ${fill("<hôte>")}${d.base_path}/`),
      h("h4", {}, "Plans et quotas"), plansTable(d.plans),
      h("h4", {}, "États d'une exécution"), table(["État", "Sens"], d.run_statuses.map((x) => [h("code", {}, x.status), rich(x.meaning)]), "États"),
      h("h4", {}, "Erreurs"),
      h("p", { class: "fx-desc" }, `Format : ${JSON.stringify(d.error_format)}`),
      table(["HTTP", "code", "Sens"], d.errors.map((x) => [String(x.status), h("code", {}, x.code), rich(x.meaning)]), "Erreurs"),
      h("h4", {}, "Protocole MCP"), h("p", { class: "fx-desc" }, [d.mcp.text, " ", h("code", {}, fill("<hôte>") + d.mcp.path)]));
  }

  function docs() {
    const shown = products.filter((p) => p.status === "published" || isAdmin());
    if (!shown.length) return empty("Aucun produit à documenter pour l'instant.");
    const side = h("div", { class: "fx-side" },
      h("h3", {}, "Produits"),
      h("div", { class: "fx-rows" }, shown.map((p) => h("button", { type: "button", class: "fx-row", "aria-current": String(p.slug === docSel), onclick: () => openDocs(p.slug) },
        h("span", { class: "fx-row-top" }, h("strong", {}, p.name), statusChip(p.status)), h("span", { class: "fx-desc" }, p.description || p.slug)))));
    let main;
    if (!docSel) main = empty("Choisissez un produit pour lire sa documentation.");
    else if (!doc) main = empty(notice && notice.kind === "error" ? "Documentation indisponible." : "Chargement de la documentation…");
    else {
      const ep = doc.endpoints.find((x) => x.id === docEp) || doc.endpoints[0];
      const callable = doc.endpoints.some((x) => x.kind === "invoke");
      main = h("div", { class: "fx-card" },
        h("div", { class: "fx-head" }, h("h3", {}, `API « ${doc.product.name} »`), statusChip(doc.product.status)),
        h("p", { class: "fx-desc" }, doc.product.description || "Aucune description."),
        h("div", { class: "fx-actions" }, h("button", { type: "button", onclick: () => downloadSpec(doc.product.slug) }, "Télécharger la description OpenAPI")),
        doc.unavailable.length ? h("p", { class: "fx-banner error", role: "status" }, `Pas encore appelable (aucune version publiée) : ${doc.unavailable.join(", ")}.`) : null,
        callable
          ? [h("div", { class: "fx-tabs sub", role: "tablist", "aria-label": "Endpoints" },
              doc.endpoints.map((x) => h("button", { type: "button", role: "tab", "aria-selected": String(x.id === ep.id), onclick: () => { docEp = x.id; draw(); } }, x.kind === "status" ? "Suivre une exécution" : x.title))),
            endpointCard(ep)]
          : empty(doc.unavailable.length
            ? "Aucun workflow appelable pour l'instant : publiez une version de ceux qui sont listés ci-dessus."
            : `Ce produit ne contient encore aucun workflow : il n'y a rien à appeler.${isAdmin() ? " Ajoutez-en dans l'onglet Administration." : " Un administrateur doit en ajouter."}`),
        docOverview(doc));
    }
    return h("div", { class: "fx-split" }, side, h("div", { class: "fx-main" }, main));
  }

  const TABS = () => [
    ["catalogue", "Catalogue", catalogue],
    ["subs", isAdmin() ? "Souscriptions" : "Mes souscriptions", mySubs],
    ["approvals", "Approbations", approvals],
    ["docs", "Documentation", docs],
    ...(isAdmin() ? [["admin", "Administration", admin]] : []),
  ];

  function draw() {
    if (disposed) return;
    const tabs = TABS();
    if (!tabs.some((t) => t[0] === tab)) tab = "catalogue";
    const pending = subs.filter((s) => s.status === "pending").length;
    el.replaceChildren(h("div", { class: "fx-store" },
      h("div", {}, h("h2", {}, "API store"), h("p", { class: "fx-sub" }, "Publiez vos workflows comme produits d'API, avec plans, quotas et clés.")),
      h("div", { class: "fx-tabs", role: "tablist", "aria-label": "Sections de l'API store" },
        tabs.map(([id, label]) => h("button", { type: "button", role: "tab", "aria-selected": String(id === tab), onclick: () => { tab = id; notice = null; draw(); } },
          label, id === "approvals" && pending > 0 ? h("span", { class: "fx-count" }, pending) : null))),
      ...[banner()].filter(Boolean),
      h("div", { role: "tabpanel" }, tabs.find((t) => t[0] === tab)[2]())));
  }

  addStyle(el);
  draw();
  load();
  return () => { disposed = true; notice = null; };
}
