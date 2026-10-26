import { useEffect, useState } from "react";
import { errorText, useWsMutation, useWsQuery } from "../../lib";

interface AiPolicy { mode: string; daily_token_budget: number; redact_cloud: boolean; log_content: boolean; used_today: number; modes: Record<string, string> }
interface Providers { results: { name: string; kind: string; base_url: string; model: string; cloud: boolean; enabled: boolean }[] }
interface Usage { used_today: number; results: { at: string; provider: string; kind: string; use_case: string; tokens: number; status: string; error_code: string; prompt_version: string }[] }
const KINDS: Record<string, string> = { local_openai: "Serveur local compatible OpenAI (llama.cpp, Ollama, vLLM)", cloud_anthropic: "Cloud : API Messages d'Anthropic (facultatif, payant)", fake: "Faux fournisseur déterministe (tests)" };

export function AiTab() {
  const pol = useWsQuery<AiPolicy>("aipolicy", "/ai/policy/");
  const prov = useWsQuery<Providers>("aiproviders", "/ai/providers/");
  const usage = useWsQuery<Usage>("aiusage", "/ai/usage/");
  const save = useWsMutation<Partial<AiPolicy>>("PUT", "/ai/policy/", ["aipolicy"]);
  const add = useWsMutation<Record<string, unknown>>("POST", "/ai/providers/", ["aiproviders"]);
  const del = useWsMutation<{ name: string }>("DELETE", (i) => `/ai/providers/${i.name}/`, ["aiproviders"]);
  const [p, setP] = useState<Partial<AiPolicy>>({});
  const [f, setF] = useState({ name: "", kind: "local_openai", base_url: "", model: "", secret: "" });
  useEffect(() => { if (pol.data) setP(pol.data); }, [pol.data]);
  return (
    <>
      <h2>Assistant IA (facultatif)</h2>
      <p className="notice">Par défaut l'IA est <strong>désactivée</strong>. En mode « local strict », aucune donnée ne quitte votre infrastructure et aucun repli vers un service externe n'est jamais tenté. Les modèles et leurs licences s'évaluent séparément du code de la plateforme.</p>
      <form onSubmit={(e) => { e.preventDefault(); save.mutate({ mode: p.mode, daily_token_budget: p.daily_token_budget, redact_cloud: p.redact_cloud, log_content: p.log_content }); }}>
        <div className="field"><label htmlFor="ai-m">Mode</label>
          <select id="ai-m" value={p.mode ?? "off"} onChange={(e) => setP({ ...p, mode: e.target.value })}>{Object.entries(pol.data?.modes ?? {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
        <div className="field"><label htmlFor="ai-b">Budget quotidien (jetons) — utilisé aujourd'hui : {pol.data?.used_today ?? 0}</label>
          <input id="ai-b" type="number" min={0} value={p.daily_token_budget ?? 0} onChange={(e) => setP({ ...p, daily_token_budget: Number(e.target.value) })} /></div>
        <div className="field field-inline"><input id="ai-r" type="checkbox" checked={p.redact_cloud ?? true} onChange={(e) => setP({ ...p, redact_cloud: e.target.checked })} /><label htmlFor="ai-r">Masquer e-mails, adresses IP et jetons avant tout envoi cloud</label></div>
        <div className="field field-inline"><input id="ai-l" type="checkbox" checked={p.log_content ?? false} onChange={(e) => setP({ ...p, log_content: e.target.checked })} /><label htmlFor="ai-l">Conserver le contenu des échanges (déconseillé : peut contenir des données sensibles)</label></div>
        <button type="submit" className="primary">Enregistrer</button>
        {save.isSuccess && <p role="status">Enregistré.</p>}
        {save.error && <p role="alert" className="field-error">Erreur : {errorText(save.error)}</p>}
      </form>
      <h3>Fournisseurs</h3>
      <ul>{prov.data?.results.map((r) => (
        <li key={r.name}>{r.name} — {KINDS[r.kind]} {r.model && `(${r.model})`} {r.cloud ? "☁ cloud" : "🏠 local"} <button type="button" onClick={() => del.mutate({ name: r.name })} aria-label={`Supprimer le fournisseur ${r.name}`}>Supprimer</button></li>
      ))}</ul>
      <form className="inline-form" onSubmit={(e) => { e.preventDefault(); add.mutate({ ...f, secret: f.secret || null }); }}>
        <div className="field"><label htmlFor="pv-n">Nom</label><input id="pv-n" value={f.name} pattern="[a-z0-9-]+" onChange={(e) => setF({ ...f, name: e.target.value })} required /></div>
        <div className="field"><label htmlFor="pv-k">Type</label><select id="pv-k" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
        <div className="field"><label htmlFor="pv-u">Adresse du serveur</label><input id="pv-u" value={f.base_url} placeholder="http://127.0.0.1:8080" onChange={(e) => setF({ ...f, base_url: e.target.value })} /></div>
        <div className="field"><label htmlFor="pv-m">Modèle</label><input id="pv-m" value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} /></div>
        <div className="field"><label htmlFor="pv-s">Secret (clé d'API, nom d'un secret)</label><input id="pv-s" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} /></div>
        <button type="submit">Ajouter</button>
      </form>
      {add.error && <p role="alert" className="field-error">Erreur : {errorText(add.error)}</p>}
      <h3>Utilisation (métadonnées uniquement)</h3>
      <div className="table-wrap"><table><caption className="sr-only">Appels d'IA récents</caption>
        <thead><tr><th scope="col">Fournisseur</th><th scope="col">Usage</th><th scope="col">Prompt</th><th scope="col">Jetons</th><th scope="col">Résultat</th></tr></thead>
        <tbody>{usage.data?.results.slice(0, 20).map((u, i) => <tr key={i}><td>{u.provider} ({u.kind})</td><td>{u.use_case}</td><td>v{u.prompt_version}</td><td>{u.tokens}</td><td>{u.status}{u.error_code ? ` (${u.error_code})` : ""}</td></tr>)}</tbody></table></div>
    </>
  );
}
