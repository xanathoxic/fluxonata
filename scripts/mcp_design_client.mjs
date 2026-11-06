// Banc MCP de conception (ADR 0025) : le client OFFICIEL du SDK MCP (Streamable HTTP) joue le rôle d'un assistant.
// Usage : node mcp_design_client.mjs <url> <fichier-jeton-design> <fichier-jeton-trop-puissant>
// Il vérifie (et sort en erreur sinon) : handshake, catalogue d'outils, brouillons et propositions, absence de tout outil de publication /
// lancement / activation, refus d'un jeton qui porte run ou publish.
import { readFileSync } from "node:fs";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const [url, designFile, bigFile] = process.argv.slice(2);
const connect = async (file) => {
  const client = new Client({ name: "fluxonata-banc-mcp", version: "1.0.0" });
  await client.connect(new StreamableHTTPClientTransport(new URL(url), { requestInit: { headers: { Authorization: `Bearer ${readFileSync(file, "utf8").trim()}` } } }));
  return client;
};
let failures = 0;
const check = (ok, label) => { console.log(`${ok ? "ok   " : "ÉCHEC"} ${label}`); if (!ok) failures++; };
const call = async (c, name, args = {}) => {
  const r = await c.callTool({ name, arguments: args });
  return { error: !!r.isError, data: r.structuredContent, text: r.content?.[0]?.text ?? "" };
};

const c = await connect(designFile);
check(c.getServerVersion()?.name === "fluxonata", "handshake : le serveur se présente");
const names = (await c.listTools()).tools.map((t) => t.name);
check(names.length === 23, `catalogue : ${names.length} outils (attendu 23)`);
check(names.includes("list_integrations"), "le catalogue d'intégrations est lisible");
check(names.includes("list_triggers"), "les déclencheurs existants sont lisibles (lecture seule)");
check(!names.filter((n) => n !== "list_triggers").some((n) => /publish|^run|activate|approve|delete|promote|secret|trigger/.test(n)), "aucun outil de publication, lancement, activation, approbation, suppression, secret");

check((await call(c, "search_actions", { query: "voyelles" })).data?.count === 0, "l'assistant constate que l'action n'existe pas");
const action = await call(c, "propose_action", {
  slug: "compter-voyelles", name: "Compter les voyelles", description: "Compte le nombre de voyelles d'un texte fourni en entrée.",
  version: {
    source: "def run(inputs):\n    return {'n': sum(ch in 'aeiouy' for ch in inputs['texte'].lower())}",
    input_schema: { type: "object", required: ["texte"], properties: { texte: { type: "string", title: "Texte", description: "Le texte à analyser." } } },
    output_schema: { type: "object", properties: { n: { type: "integer", title: "Nombre", description: "Nombre de voyelles trouvées." } } },
  },
});
check(!action.error && action.data?.status === "proposed", "l'action est créée en proposition");
const weak = await call(c, "propose_action", { slug: "faible", name: "Faible", description: "court", version: { source: "def run(i):\n    return {}", input_schema: { type: "object" }, output_schema: { type: "object" } } });
check(weak.error && weak.text.startsWith("Refusé"), "une proposition sans contrat complet est refusée avec des erreurs structurées");
const form = await call(c, "create_form", { slug: "saisie-texte", name: "Saisie d'un texte", definition: { version: 1, sections: [{ id: "s1", title: "Texte", fields: [{ key: "texte", type: "text", label: "Texte", required: true }] }] } });
check(!form.error && /brouillon/.test(form.data?.status), "le formulaire est créé en brouillon");
const wf = await call(c, "create_workflow", {
  slug: "analyse-texte", name: "Analyse d'un texte",
  graph: { inputs: { type: "object", required: ["texte"], properties: { texte: { type: "string" } } }, nodes: [{ id: "n1", type: "action", name: "Compter", action: "compter-voyelles", version: "1.0.0", inputs: { texte: "{{ inputs.texte }}" } }], edges: [], outputs: { n: "{{ steps.n1.output.n }}" } },
});
check(!wf.error && wf.data?.issues?.some((i) => i.code === "proposed_action"), "le workflow signale que l'action proposée n'est pas relue");
const stale = await call(c, "update_workflow", { slug: "analyse-texte", revision: 1, graph: {} });
check(stale.error && /current_revision/.test(stale.text), "une révision périmée est refusée");
const att = await call(c, "attach_form", { workflow: "analyse-texte", revision: wf.data.revision, form: "saisie-texte" });
check(!att.error && /copie/.test(att.data?.attached ?? ""), "le formulaire non publié est rattaché par copie");
check(!(await call(c, "validate_workflow", { slug: "analyse-texte" })).error, "validate_workflow répond");
// Essai à blanc : exécuté par le WORKER (jamais par l'API) ; l'assistant lit les valeurs réelles et un bug réel
const script = (id, code, bindings, outputs) => ({ id, type: "script", name: id, language: "python", script: code, bindings, outputs });
const calc = (code) => ({
  inputs: { type: "object", required: ["pays", "montant"], properties: { pays: { type: "string" }, montant: { type: "number" } } },
  nodes: [
    script("taux", "taux = {'fr': 0.2, 'de': 0.19}.get(pays, 0.0)\n", { pays: "{{ inputs.pays }}" }, [{ name: "taux", type: "number" }]),
    script("ttc", code, { montant: "{{ inputs.montant }}", taux: "{{ steps.taux.output.taux }}" }, [{ name: "total", type: "number" }]),
  ],
  edges: [{ from: "taux", to: "ttc" }], outputs: { total: "{{ steps.ttc.output.total }}" },
});
const dry = async (slug, samples) => {
  const started = await call(c, "start_dry_run", { slug, samples });
  if (started.error) return { error: started.text };
  for (let i = 0; i < 90; i++) {
    const r = await call(c, "get_dry_run", { job_id: started.data.job_id });
    if (r.data?.status === "succeeded") return r.data.result;
    if (r.data?.status === "failed" || r.error) return { error: r.text || JSON.stringify(r.data?.error) };
    await new Promise((ok) => setTimeout(ok, 1000));
  }
  return { error: "délai dépassé (le worker n'a pas traité l'essai en 90 s)" };
};
await call(c, "create_workflow", { slug: "calcul-ttc", name: "Calcul TTC", graph: calc("total = round(montant * (1 + taux), 2)\n") });
const good = await dry("calcul-ttc", [{ label: "France", inputs: { pays: "fr", montant: 100 } }]);
const out = good.scenarios?.[0]?.steps?.find((x) => x.id === "ttc")?.output?.total;
check(good.ran === true && Number(out) === 120, `essai à blanc : les VRAIES valeurs reviennent (total = ${out}, attendu 120)`);
check(good.problems?.length === 0 && good.coverage?.executed === 2, "essai à blanc : aucun problème, couverture complète");
await call(c, "create_workflow", { slug: "calcul-bug", name: "Calcul avec bug", graph: calc("total = round(montant * (1 + taux) / (taux - 0.2), 2)\n") });
const bad = await dry("calcul-bug", [{ label: "France", inputs: { pays: "fr", montant: 100 } }]);
check(bad.problems?.length > 0 && /division/i.test(JSON.stringify(bad)), "essai à blanc : un script qui plante est signalé avec la cause");
// Test du CODE d'une action proposée : dans le bac à sable (conteneur), sans réseau ni secret ; l'action reste « proposée »
const testAction = async (args) => {
  const started = await call(c, "start_action_test", args);
  if (started.error) return { error: started.text };
  for (let i = 0; i < 90; i++) {
    const r = await call(c, "get_dry_run", { job_id: started.data.job_id });
    if (r.data?.status === "succeeded") return r.data.result;
    if (r.data?.status === "failed" || r.error) return { error: r.text || JSON.stringify(r.data?.error) };
    await new Promise((ok) => setTimeout(ok, 1000));
  }
  return { error: "délai dépassé" };
};
const tested = await testAction({ slug: "compter-voyelles", cases: [
  { label: "bonjour", inputs: { texte: "Bonjour" }, expected: { n: 3 } },
  { label: "mauvaise attente", inputs: { texte: "abc" }, expected: { n: 5 } },
] });
check(tested.cases?.[0]?.ok === true && JSON.parse(tested.cases[0].output).n === 3, "test d'action : le vrai code s'exécute dans le bac à sable et rend la vraie valeur");
check(tested.cases?.[1]?.ok === false && tested.cases[1].problems.some((p) => p.code === "EXPECTED"), "test d'action : une attente fausse est signalée");
for (const tool of ["publish_workflow", "run_workflow", "activate_action", "delete_workflow", "create_connection"]) {
  check((await call(c, tool, { slug: "analyse-texte" })).error, `outil inexistant : ${tool}`);
}
await c.close();

let refused = false;
try { await (await connect(bigFile)).close(); } catch { refused = true; }
check(refused, "un jeton qui porte aussi run / publish est refusé");

console.log(failures ? `\n${failures} vérification(s) en échec` : "\nBanc MCP de conception : toutes les vérifications passent");
process.exit(failures ? 1 : 0);
