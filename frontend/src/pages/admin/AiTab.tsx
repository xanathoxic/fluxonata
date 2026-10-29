import { useEffect, useState } from "react";
import { Badge, Confirm, EmptyRow, Section, usePaged } from "../../components/AdminUi";
import { Overlay } from "../../components/Overlay";
import { Kpi } from "../OpsCharts";
import { AiEvals } from "./AiEvals";
import { errorText, useWsMutation, useWsQuery } from "../../lib";

interface AiPolicy { mode: string; daily_token_budget: number; redact_cloud: boolean; log_content: boolean; dry_run: boolean; used_today: number; modes: Record<string, string> }
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
  const [adding, setAdding] = useState(false);
  const [toDelete, setToDelete] = useState<string | null>(null);
  useEffect(() => { if (pol.data) setP(pol.data); }, [pol.data]);
  const providers = prov.data?.results ?? [];
  const paged = usePaged(usage.data?.results ?? [], 10);
  const budget = p.daily_token_budget ?? 0;
  const used = pol.data?.used_today ?? 0;
  return (
    <>
      <ul className="kpis" aria-label="Chiffres de l'assistant">
        <Kpi label="Mode" value={pol.data?.modes[pol.data.mode] ? (pol.data.mode === "off" ? "Désactivée" : "Activée") : "—"} tone={pol.data?.mode === "off" ? undefined : "ok"} />
        <Kpi label="Jetons aujourd'hui" value={used.toLocaleString("fr-FR")} hint={budget > 0 ? `sur ${budget.toLocaleString("fr-FR")}` : "sans budget"} share={budget > 0 ? Math.min(1, used / budget) : null} />
        <Kpi label="Fournisseurs" value={providers.length} hint={`${providers.filter((r) => !r.cloud).length} local, ${providers.filter((r) => r.cloud).length} cloud`} tone="info" />
      </ul>
      <Section title="Assistant IA (facultatif)" hint={<>Par défaut l'IA est <strong>désactivée</strong>. En mode « local strict », aucune donnée ne quitte votre infrastructure et aucun repli vers un service externe n'est jamais tenté. Les modèles et leurs licences s'évaluent séparément du code de la plateforme.</>}>
        <form onSubmit={(e) => { e.preventDefault(); save.mutate({ mode: p.mode, daily_token_budget: p.daily_token_budget, redact_cloud: p.redact_cloud, log_content: p.log_content, dry_run: p.dry_run }); }}>
          <div className="adm-two">
            <div className="field"><label htmlFor="ai-m">Mode</label>
              <select id="ai-m" value={p.mode ?? "off"} onChange={(e) => setP({ ...p, mode: e.target.value })}>{Object.entries(pol.data?.modes ?? {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
            <div className="field"><label htmlFor="ai-b">Budget quotidien (jetons) — utilisé aujourd'hui : {used}</label>
              <input id="ai-b" type="number" min={0} value={p.daily_token_budget ?? 0} onChange={(e) => setP({ ...p, daily_token_budget: Number(e.target.value) })} /></div>
          </div>
          <fieldset className="adm-rules"><legend>Confidentialité et essais</legend>
            <div className="adm-rule"><input id="ai-r" className="adm-switch" type="checkbox" checked={p.redact_cloud ?? true} onChange={(e) => setP({ ...p, redact_cloud: e.target.checked })} /><div><label htmlFor="ai-r">Masquer e-mails, adresses IP et jetons avant tout envoi cloud</label></div></div>
            <div className="adm-rule"><input id="ai-d" className="adm-switch" type="checkbox" checked={p.dry_run ?? true} onChange={(e) => setP({ ...p, dry_run: e.target.checked })} /><div><label htmlFor="ai-d">Autoriser l'essai à blanc : l'assistant exécute les scripts d'un brouillon dans le bac à sable (sans réseau ni secrets) sur des jeux d'essai</label></div></div>
            <div className="adm-rule"><input id="ai-l" className="adm-switch" type="checkbox" checked={p.log_content ?? false} onChange={(e) => setP({ ...p, log_content: e.target.checked })} /><div><label htmlFor="ai-l">Conserver le contenu des échanges (déconseillé : peut contenir des données sensibles)</label></div></div>
          </fieldset>
          <div className="adm-savebar">
            <button type="submit" className="primary">Enregistrer</button>
            {save.isSuccess && <span role="status" className="ok-text">Enregistré.</span>}
          </div>
          {save.error && <p role="alert" className="field-error">Erreur : {errorText(save.error)}</p>}
        </form>
      </Section>

      <Section title="Fournisseurs" hint="Le serveur ou le service qui répond aux demandes de l'assistant. Un fournisseur local garde les données chez vous."
        actions={<button type="button" onClick={() => { add.reset(); setAdding(true); }}>Ajouter un fournisseur</button>}>
        {providers.length === 0 && <p className="hint">Aucun fournisseur configuré : l'assistant ne peut pas répondre.</p>}
        <ul className="adm-providers">{providers.map((r) => (
          <li key={r.name}>
            <div><span className="adm-prov-line">{r.name} — {KINDS[r.kind]} {r.model && `(${r.model})`}</span><br /><Badge tone={r.cloud ? "warn" : "ok"}>{r.cloud ? "☁ cloud" : "🏠 local"}</Badge> {!r.enabled && <Badge tone="muted">désactivé</Badge>}</div>
            <button type="button" onClick={() => setToDelete(r.name)} aria-label={`Supprimer le fournisseur ${r.name}`}>Supprimer</button>
          </li>
        ))}</ul>
      </Section>

      <Section title="Utilisation (métadonnées uniquement)" hint="Les appels récents : qui a répondu, pour quel usage et combien de jetons. Le contenu des échanges n'est pas conservé par défaut.">
        <div className="table-wrap"><table className="adm-table"><caption className="sr-only">Appels d'IA récents</caption>
          <thead><tr><th scope="col">Fournisseur</th><th scope="col">Usage</th><th scope="col">Prompt</th><th scope="col">Jetons</th><th scope="col">Résultat</th></tr></thead>
          <tbody>{paged.slice.map((u, i) => <tr key={i}><td>{u.provider} ({u.kind})</td><td>{u.use_case}</td><td>v{u.prompt_version}</td><td>{u.tokens}</td><td><Badge tone={u.status === "ok" || u.status === "succeeded" ? "ok" : "bad"}>{u.status}{u.error_code ? ` (${u.error_code})` : ""}</Badge></td></tr>)}
            {(usage.data?.results.length ?? 0) === 0 && <EmptyRow cols={5}>Aucun appel pour le moment.</EmptyRow>}</tbody></table></div>
        {paged.pager("Pagination de l'utilisation")}
      </Section>
      <AiEvals />

      {toDelete && (
        <Confirm title="Supprimer ce fournisseur ?" confirmLabel="Supprimer le fournisseur" busy={del.isPending} onCancel={() => setToDelete(null)} onConfirm={() => { del.mutate({ name: toDelete }); setToDelete(null); }}>
          <p>Le fournisseur <strong>{toDelete}</strong> sera retiré. L'assistant ne pourra plus l'utiliser ; son secret éventuel n'est pas supprimé.</p>
        </Confirm>
      )}
      {adding && (
        <Overlay title="Ajouter un fournisseur" onClose={() => setAdding(false)}
          footer={<><button type="button" onClick={() => setAdding(false)}>Annuler</button><button type="submit" form="prov-form" className="primary">Ajouter</button></>}>
          <form id="prov-form" onSubmit={(e) => { e.preventDefault(); add.mutate({ ...f, secret: f.secret || null }, { onSuccess: () => { setAdding(false); setF({ name: "", kind: "local_openai", base_url: "", model: "", secret: "" }); } }); }}>
            <div className="field"><label htmlFor="pv-n">Nom</label><input id="pv-n" value={f.name} pattern="[a-z0-9-]+" onChange={(e) => setF({ ...f, name: e.target.value })} required /></div>
            <div className="field"><label htmlFor="pv-k">Type</label><select id="pv-k" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
            <div className="field"><label htmlFor="pv-u">Adresse du serveur</label><input id="pv-u" value={f.base_url} placeholder="http://127.0.0.1:8080" onChange={(e) => setF({ ...f, base_url: e.target.value })} /></div>
            <div className="field"><label htmlFor="pv-m">Modèle</label><input id="pv-m" value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} /></div>
            <div className="field"><label htmlFor="pv-s">Secret (clé d'API, nom d'un secret)</label><input id="pv-s" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} /></div>
            {add.error && <p role="alert" className="field-error">Erreur : {errorText(add.error)}</p>}
          </form>
        </Overlay>
      )}
    </>
  );
}
