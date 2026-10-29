import { BrandIcon } from "../components/BrandIcon";
import { useState } from "react";
import type { Integration, IntegrationParam } from "../api";
import { useWsQuery } from "../lib";
import type { GraphNode } from "./types";
import { HelpTip } from "../components/HelpTip";

interface Conn { name: string; label: string; kind: string; config: { base_url?: string } }

/** JSON object/value typed as text: kept as text while it does not parse, saved as soon as it does. */
export function JsonField({ id, label, value, onChange, hint, rows = 3 }: { id: string; label: string; value: unknown; onChange: (v: unknown) => void; hint?: string; rows?: number }) {
  const [text, setText] = useState(value === undefined || value === "" ? "" : typeof value === "string" ? value : JSON.stringify(value, null, 2));
  const [bad, setBad] = useState(false);
  return (
    <div className="field"><label htmlFor={id}>{label}</label>
      <textarea id={id} rows={rows} spellCheck={false} value={text} aria-invalid={bad} onChange={(e) => {
        const t = e.target.value;
        setText(t);
        if (!t.trim()) { setBad(false); onChange(undefined); return; }
        try { onChange(JSON.parse(t)); setBad(false); } catch { setBad(true); }
      }} />
      {bad ? <p role="alert" className="field-error">JSON invalide : la valeur n'est pas encore enregistrée.</p> : hint && <HelpTip>{hint}</HelpTip>}
    </div>
  );
}

const NUMERIC = /^-?\d+(\.\d+)?$/;

/** One parameter of an integration operation: literal or expression ({{ … }}); typed values are kept typed. */
export function ParamField({ prm, value, onChange }: { prm: IntegrationParam; value: unknown; onChange: (v: unknown) => void }) {
  const id = `ip-${prm.name}`;
  const label = `${prm.label}${prm.required ? " *" : ""}`;
  const hint = prm.help ?? (prm.example !== undefined && typeof prm.example !== "object" ? `ex. ${String(prm.example)}` : undefined);
  if (prm.type === "object" || prm.type === "array") {
    return <JsonField id={id} label={label} value={value} onChange={onChange} rows={4}
      hint={prm.example !== undefined ? `ex. ${JSON.stringify(prm.example)}` : prm.default !== undefined ? `par défaut : ${JSON.stringify(prm.default)}` : "JSON ; les valeurs peuvent contenir {{ expressions }}"} />;
  }
  if (prm.type === "boolean") {
    return <div className="field"><label htmlFor={id}>{label}</label>
      <select id={id} value={value === true ? "true" : value === false ? "false" : ""} onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value === "true")}>
        <option value="">{prm.default !== undefined ? `Par défaut (${String(prm.default)})` : "Non renseigné"}</option><option value="true">Oui</option><option value="false">Non</option></select></div>;
  }
  const numeric = prm.type === "integer" || prm.type === "number";
  const shown = value === undefined || value === null ? "" : typeof value === "string" ? value : String(value);
  const common = { id, value: shown, placeholder: prm.default !== undefined ? `par défaut : ${String(prm.default)}` : undefined,
    onChange: (e: { target: { value: string } }) => { const t = e.target.value; onChange(t === "" ? undefined : numeric && NUMERIC.test(t) ? Number(t) : t); } };
  return <div className="field"><label htmlFor={id}>{label}</label>
    {prm.type === "text" ? <textarea rows={3} {...common} /> : <input {...common} />}
    {hint && <HelpTip>{hint}</HelpTip>}</div>;
}

function IntegrationConfig({ node, onUpdate, integ, conns }: { node: GraphNode; onUpdate: (patch: Partial<GraphNode>) => void; integ: Integration; conns: { name: string; label: string; kind: string }[] }) {
  const op = integ.operations.find((o) => o.id === node.operation) ?? integ.operations[0]!;
  const usable = conns.filter((c) => c.kind === integ.connection.kind);
  const params = node.params ?? {};
  return (
    <>
      <p className="brand-line"><BrandIcon slug={integ.slug} name={integ.name} size={34} /><span><strong>{integ.name}</strong><br /><span className="hint">{integ.category}</span></span></p>
      <p className="hint">{integ.description} {integ.connection.setup}</p>
      <div className="field"><label htmlFor="int-conn">Connexion</label>
        <select id="int-conn" value={node.connection ?? ""} onChange={(e) => onUpdate({ connection: e.target.value })}>
          <option value="">Choisir…</option>
          {(usable.length ? usable : conns).map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}
        </select>
        {usable.length === 0 && <p className="hint">Aucune connexion de ce type : créez-en une dans Connexions (type « {integ.connection.kind} », adresse {integ.connection.config.base_url}).</p>}</div>
      <div className="field">
<HelpTip>{op.description} ({op.method} {op.path})</HelpTip><label htmlFor="int-op">Opération</label>
        <select id="int-op" value={op.id} onChange={(e) => { const o = integ.operations.find((x) => x.id === e.target.value)!; onUpdate({ operation: o.id, params: {}, name: `${integ.name} — ${o.name}` }); }}>
          {integ.operations.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
        </select></div>
      {op.params.map((prm) => <ParamField key={`${op.id}-${prm.name}`} prm={prm} value={params[prm.name]} onChange={(v) => { const next = { ...params }; if (v === undefined) delete next[prm.name]; else next[prm.name] = v; onUpdate({ params: next }); }} />)}
      <div className="field">
<HelpTip>Sorties : <code>steps.{node.id}.output.body</code> (réponse du service), <code>.status</code>, <code>.ok</code>.</HelpTip><label htmlFor="int-mode">Mode</label>
        <select id="int-mode" value={node.mode ?? "test"} onChange={(e) => onUpdate({ mode: e.target.value as "test" | "reel" })}>
          <option value="test">Test : aucune requête n'est envoyée (sortie d'exemple)</option>
          <option value="reel">Réel : la requête est envoyée</option>
        </select></div>
    </>
  );
}

export function HttpConfig({ node, onUpdate }: { node: GraphNode; onUpdate: (patch: Partial<GraphNode>) => void }) {
  const integrations = useWsQuery<{ results: Integration[] }>("integrations", "/integrations/");
  const conns = useWsQuery<{ results: Conn[] }>("connections", "/connections/");
  const http = (conns.data?.results ?? []).filter((c) => ["http_bearer", "api_key", "basic", "oauth2_client", "oauth2_code", "aws_sigv4"].includes(c.kind));
  const current = http.find((c) => c.name === node.connection);
  const integ = node.integration ? integrations.data?.results.find((i) => i.slug === node.integration) : undefined;
  if (integ) return <IntegrationConfig node={node} onUpdate={onUpdate} integ={integ} conns={http} />;
  return (
    <>
      <div className="field">
<HelpTip>{current?.config.base_url ? `Adresse de base : ${current.config.base_url}. Les identifiants de la connexion sont ajoutés par la plateforme.` : "L'adresse et le jeton viennent de la connexion (menu Connexions, type « Service HTTP ») : le workflow ne peut pas viser un autre hôte."}</HelpTip><label htmlFor="http-conn">Connexion (service HTTP)</label>
        <select id="http-conn" value={node.connection ?? ""} onChange={(e) => onUpdate({ connection: e.target.value })}>
          <option value="">Choisir…</option>
          {http.map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}
        </select></div>
      <div className="field"><label htmlFor="http-method">Méthode</label>
        <select id="http-method" value={node.method ?? "GET"} onChange={(e) => onUpdate({ method: e.target.value as GraphNode["method"] })}>
          {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => <option key={m}>{m}</option>)}
        </select></div>
      <div className="field"><label htmlFor="http-path">Chemin</label>
        <input id="http-path" value={node.path ?? "/"} placeholder="/api/v1/machines/{{ inputs.id }}" onChange={(e) => onUpdate({ path: e.target.value })} /></div>
      <JsonField id="http-query" label="Paramètres de requête (JSON)" value={node.query} onChange={(v) => onUpdate({ query: v as Record<string, unknown> | undefined })} hint={'ex. {"page": 1, "nom": "{{ inputs.nom }}"}'} />
      <JsonField id="http-headers" label="En-têtes (JSON)" value={node.headers} onChange={(v) => onUpdate({ headers: v as Record<string, unknown> | undefined })} hint="Authorization, Host, Cookie… sont réservés à la plateforme." />
      {node.method !== "GET" && (
        <div className="field">
<HelpTip>Pour « formulaire » et « multipart », le corps est un objet de champs. Multipart : 10 fichiers au plus, chacun en texte (<code>content</code>) ou issu d'une étape (<code>file</code>). « Fichier brut » envoie le fichier tel quel.</HelpTip><label htmlFor="http-bf">Format du corps</label>
          <select id="http-bf" value={node.body_format ?? "json"} onChange={(e) => onUpdate({ body_format: e.target.value as GraphNode["body_format"] })}>
            <option value="json">JSON</option><option value="form">Formulaire (application/x-www-form-urlencoded)</option><option value="multipart">Multipart (champs + fichiers)</option><option value="file">Fichier brut (le corps est un fichier)</option></select></div>)}
      {node.method !== "GET" && node.body_format === "file" && <div className="field">
<HelpTip>La sortie <code>file</code> d'une étape précédente de cette exécution (10 Mo au plus). Son type devient l'en-tête Content-Type, sauf si vous en indiquez un.</HelpTip><label htmlFor="http-file">Fichier à envoyer</label>
        <input id="http-file" value={typeof node.file === "string" ? node.file : ""} placeholder="{{ steps.telecharger.output.file }}" onChange={(e) => onUpdate({ file: e.target.value })} /></div>}
      {node.method !== "GET" && node.body_format !== "file" && <JsonField id="http-body" label="Corps (JSON ou texte)" value={node.body} rows={5} onChange={(v) => onUpdate({ body: v })} hint={'ex. {"nom": "{{ inputs.nom }}"}'} />}
      {node.method !== "GET" && node.body_format === "multipart" && <JsonField id="http-files" label="Fichiers texte (JSON)" value={node.files} rows={4} onChange={(v) => onUpdate({ files: v as GraphNode["files"] })} hint={'ex. [{"field": "fichier", "filename": "rapport.csv", "content": "{{ steps.t.output.csv }}"}] ou [{"field": "image", "file": "{{ steps.dl.output.file }}"}]'} />}
      {!node.pagination && <div className="field field-inline">
<HelpTip>Pour une image, un PDF, une archive… : les octets sont gardés avec l'exécution (10 Mo au plus, 50 Mo par exécution) et la sortie <code>steps.{node.id}.output.file</code> sert de référence aux étapes suivantes ; ils sont téléchargeables depuis la page de l'exécution.</HelpTip><input id="http-rf" type="checkbox" checked={!!node.response_file} onChange={(e) => onUpdate({ response_file: e.target.checked })} /><label htmlFor="http-rf">Enregistrer la réponse comme fichier</label></div>}
      <div className="field">
<HelpTip>Les pages sont lues l'une après l'autre et leurs éléments mis bout à bout dans <code>body</code> (<code>pages</code> donne le nombre de pages lues).</HelpTip><label htmlFor="http-pg">Pagination</label>
        <select id="http-pg" value={node.pagination?.type ?? ""} onChange={(e) => onUpdate({ pagination: e.target.value ? { ...(node.pagination ?? {}), type: e.target.value as NonNullable<GraphNode["pagination"]>["type"] } : undefined })}>
          <option value="">Aucune (une seule requête)</option>
          <option value="page">Par numéro de page</option>
          <option value="offset">Par décalage (offset)</option>
          <option value="cursor">Par curseur</option>
          <option value="link">Par lien « suivant » (en-tête Link)</option>
        </select></div>
      {node.pagination && (
        <>
          <div className="field"><label htmlFor="http-pg-items">Chemin de la liste dans la réponse (vide si la réponse est la liste)</label>
            <input id="http-pg-items" value={node.pagination.items_path ?? ""} placeholder="data.items" onChange={(e) => onUpdate({ pagination: { ...node.pagination!, items_path: e.target.value } })} /></div>
          {node.pagination.type === "cursor" && <div className="field"><label htmlFor="http-pg-next">Chemin du curseur suivant dans la réponse</label>
            <input id="http-pg-next" value={node.pagination.next_path ?? ""} placeholder="meta.next" onChange={(e) => onUpdate({ pagination: { ...node.pagination!, next_path: e.target.value } })} /></div>}
          {(node.pagination.type === "page" || node.pagination.type === "offset") && <div className="field"><label htmlFor="http-pg-size">Taille de page (facultatif)</label>
            <input id="http-pg-size" type="number" min={1} value={node.pagination.page_size ?? ""} onChange={(e) => onUpdate({ pagination: { ...node.pagination!, page_size: e.target.value ? Number(e.target.value) : undefined } })} /></div>}
          <div className="field"><label htmlFor="http-pg-max">Nombre maximal de pages (1 à 50)</label>
            <input id="http-pg-max" type="number" min={1} max={50} value={node.pagination.max_pages ?? 10} onChange={(e) => onUpdate({ pagination: { ...node.pagination!, max_pages: Number(e.target.value) } })} /></div>
        </>
      )}
      <div className="field">
<HelpTip>Réponses 429 et 503 (et le délai « Retry-After » du service) pour toutes les méthodes ; erreurs réseau et 5xx seulement pour GET, PUT et DELETE : un POST peut avoir été reçu, il n'est jamais rejoué à l'aveugle.</HelpTip><label htmlFor="http-retry">Tentatives en cas de service occupé ou en panne</label>
        <select id="http-retry" value={node.retry?.max_attempts ?? 1} onChange={(e) => { const n = Number(e.target.value); onUpdate({ retry: n > 1 ? { max_attempts: n, backoff_seconds: node.retry?.backoff_seconds ?? 2, jitter: 0 } : undefined }); }}>
          {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n === 1 ? "Une seule (pas de nouvelle tentative)" : `${n} tentatives au plus`}</option>)}
        </select></div>
      <div className="field"><label htmlFor="http-timeout">Délai maximum (secondes, 1 à 60)</label>
        <input id="http-timeout" type="number" min={1} max={60} value={node.timeout_seconds ?? 15} onChange={(e) => onUpdate({ timeout_seconds: Number(e.target.value) })} /></div>
      <div className="field field-inline"><input id="http-fail" type="checkbox" checked={node.fail_on_status !== false} onChange={(e) => onUpdate({ fail_on_status: e.target.checked })} />
        <label htmlFor="http-fail">Échouer si le service répond autre chose que 2xx</label></div>
      <div className="field">
<HelpTip>Sorties : <code>steps.{node.id}.output.status</code>, <code>.ok</code>, <code>.body</code> (JSON décodé si le service en renvoie), <code>.simulated</code>.</HelpTip><label htmlFor="http-mode">Mode</label>
        <select id="http-mode" value={node.mode ?? "test"} onChange={(e) => onUpdate({ mode: e.target.value as "test" | "reel" })}>
          <option value="test">Test : aucune requête n'est envoyée (sortie simulée)</option>
          <option value="reel">Réel : la requête est envoyée</option>
        </select></div>
    </>
  );
}
