import type { Integration } from "../api";
import { useMemo, useState } from "react";
import { BrandIcon } from "../components/BrandIcon";
import { EMPTY_SECRET, SecretSource, secretPayload, secretReady, type SecretDraft } from "../components/SecretSource";
import { BRANDS } from "../components/brandIconData";
import { Badge, Confirm, EmptyRow, SearchBox, SortTh, usePaged, useSort } from "../components/AdminUi";
import { Menu } from "../components/Menu";
import { Overlay } from "../components/Overlay";
import { PageHeader } from "../components/PageHeader";
import { IconLabel } from "../forms/FormIcon";
import { useApp } from "../context";
import { formatDate } from "../i18n";
import { errorText, useWsMutation, useWsQuery } from "../lib";
import { HelpTip } from "../components/HelpTip";

interface Conn { id: number; name: string; label: string; kind: string; config: { base_url?: string }; secret: string | null; secret_provider?: string | null; environment: string | null; last_test_ok: boolean | null; last_test_at: string | null; last_test_message: string }
const KIND: Record<string, string> = {
  http_bearer: "Service HTTP — jeton porteur",
  api_key: "Service HTTP — clé d'API",
  basic: "Service HTTP — identifiant + mot de passe",
  oauth2_client: "Service HTTP — OAuth2 (identifiants client)",
  oauth2_code: "Service HTTP — OAuth2 (autorisation par une personne)",
  aws_sigv4: "Stockage S3 / AWS (signature SigV4)",
  postgres: "Base PostgreSQL (lecture seule)",
  mysql: "Base MySQL / MariaDB (lecture seule)",
  ldap: "Annuaire LDAP / Active Directory (lecture seule)",
  sftp: "Serveur SFTP (fichiers texte)",
  smtp: "Serveur SMTP (envoi d'e-mails)",
  imap: "Boîte e-mail (IMAP, TLS)",
  generic: "Générique (un secret)",
};
interface CfgField { key: string; label: string; placeholder?: string; optional?: boolean; options?: string[] }
const URL_HINT = "https://api.exemple.org/";
const CONFIG: Record<string, CfgField[]> = {
  http_bearer: [{ key: "base_url", label: "Adresse du service", placeholder: URL_HINT }],
  api_key: [{ key: "base_url", label: "Adresse du service", placeholder: URL_HINT }, { key: "name", label: "Nom de l'en-tête ou du paramètre", placeholder: "X-API-Key", optional: true }, { key: "in", label: "Où envoyer la clé", options: ["header", "query"], optional: true }],
  basic: [{ key: "base_url", label: "Adresse du service", placeholder: URL_HINT }, { key: "username", label: "Identifiant" }],
  oauth2_client: [{ key: "base_url", label: "Adresse du service", placeholder: URL_HINT }, { key: "token_url", label: "Adresse d'obtention du jeton", placeholder: "https://auth.exemple.org/token" }, { key: "client_id", label: "Identifiant client" }, { key: "scope", label: "Périmètre (scope)", optional: true }],
  aws_sigv4: [{ key: "base_url", label: "Adresse du service", placeholder: "https://s3.eu-west-3.amazonaws.com" }, { key: "access_key_id", label: "Access Key ID" }, { key: "region", label: "Région", placeholder: "eu-west-3" }, { key: "service", label: "Service", placeholder: "s3", optional: true }],
  postgres: [{ key: "host", label: "Serveur", placeholder: "db.exemple.org" }, { key: "port", label: "Port", placeholder: "5432", optional: true }, { key: "database", label: "Base de données" }, { key: "username", label: "Utilisateur (compte en lecture seule)" }, { key: "sslmode", label: "Mode TLS", placeholder: "require", optional: true }],
  mysql: [{ key: "host", label: "Serveur", placeholder: "db.exemple.org" }, { key: "port", label: "Port", placeholder: "3306", optional: true }, { key: "database", label: "Base de données" }, { key: "username", label: "Utilisateur (compte en lecture seule)" }, { key: "tls", label: "TLS (require ou disable)", placeholder: "require", optional: true }],
  ldap: [{ key: "host", label: "Serveur", placeholder: "ldap.exemple.org" }, { key: "port", label: "Port", placeholder: "389 (636 en LDAPS)", optional: true }, { key: "bind_dn", label: "Compte de lecture (DN)", placeholder: "cn=lecteur,dc=exemple,dc=org" }, { key: "base_dn", label: "Racine de recherche", placeholder: "dc=exemple,dc=org" }, { key: "tls", label: "TLS (starttls, ldaps ou none)", placeholder: "starttls", optional: true }],
  sftp: [{ key: "host", label: "Serveur", placeholder: "sftp.exemple.org" }, { key: "port", label: "Port", placeholder: "22", optional: true }, { key: "username", label: "Identifiant" }, { key: "host_key_sha256", label: "Empreinte de la clé du serveur (SHA256:…)", optional: true }],
  smtp: [{ key: "host", label: "Serveur SMTP", placeholder: "smtp.gmail.com" }, { key: "port", label: "Port", placeholder: "465 (SSL) ou 587 (STARTTLS)", optional: true }, { key: "tls", label: "Sécurisation (ssl ou starttls)", placeholder: "ssl", optional: true }, { key: "username", label: "Identifiant (adresse du compte)" }, { key: "from_address", label: "Adresse d'expéditeur", placeholder: "compte@exemple.org" }, { key: "from_name", label: "Nom d'expéditeur", optional: true }],
  imap: [{ key: "host", label: "Serveur IMAP", placeholder: "imap.exemple.org" }, { key: "port", label: "Port", placeholder: "993", optional: true }, { key: "username", label: "Identifiant" }, { key: "folder", label: "Dossier", placeholder: "INBOX", optional: true }],
  oauth2_code: [{ key: "base_url", label: "Adresse du service", placeholder: URL_HINT }, { key: "authorize_url", label: "Adresse d'autorisation", placeholder: "https://auth.exemple.org/authorize" }, { key: "token_url", label: "Adresse d'obtention du jeton", placeholder: "https://auth.exemple.org/token" }, { key: "client_id", label: "Identifiant client" }, { key: "scope", label: "Périmètre (scope)", optional: true }],
};
const DYNAMIC_KINDS = ["postgres", "mysql", "ldap", "sftp", "smtp", "imap", "basic"]; // kinds whose login is a user name + password
const ENV: Record<string, string> = { test: "Recette", prod: "Production", dev: "Développement" };

export function ConnectionsPage() {
  const { can } = useApp();
  const q = useWsQuery<{ results: Conn[] }>("connections", "/connections/");
  const create = useWsMutation<Record<string, unknown>, Conn>("POST", "/connections/", ["connections"]);
  const test = useWsMutation<{ id: number }, { ok: boolean; message: string }>("POST", (i) => `/connections/${i.id}/test/`, ["connections"]);
  const integrations = useWsQuery<{ results: Integration[] }>("integrations", "/integrations/").data?.results ?? [];
  const [setup, setSetup] = useState("");
  const authorize = useWsMutation<{ id: number }, { authorize_url: string }>("POST", (i) => `/connections/${i.id}/oauth/start/`);
  const oauthResult = new URLSearchParams(window.location.search).get("oauth");
  const del = useWsMutation<{ id: number }>("DELETE", (i) => `/connections/${i.id}/`, ["connections"]);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [toDelete, setToDelete] = useState<Conn | null>(null);
  const [step, setStep] = useState(0);
  const [f, setF] = useState({ kind: "generic", label: "", name: "", cfg: {} as Record<string, string>, environment: "", src: EMPTY_SECRET as SecretDraft });
  const [created, setCreated] = useState<Conn | null>(null);
  const manage = can("manage_connections");
  const reset = () => { setSetup(""); setStep(0); setCreated(null); setF({ kind: "generic", label: "", name: "", cfg: {} as Record<string, string>, environment: "", src: EMPTY_SECRET }); };
  const all = q.data?.results ?? [];
  const filtered = useMemo(() => all.filter((c) => (!kindFilter || c.kind === kindFilter) && `${c.label} ${c.name}`.toLowerCase().includes(search.trim().toLowerCase())), [all, search, kindFilter]);
  const { sorted, sort, toggle } = useSort(filtered, { label: (c) => c.label, kind: (c) => KIND[c.kind] ?? c.kind, env: (c) => c.environment ?? "", test: (c) => (c.last_test_ok === null ? 0 : c.last_test_ok ? 2 : 1) }, { key: "label", dir: "asc" });
  const paged = usePaged(sorted, 10);
  const dynamic = f.src.provider === "vault" && f.src.mode === "dynamic";
  const userKeys = ["username", "bind_dn"]; // with dynamic Vault credentials, Vault supplies the user name
  const missingUser = !dynamic && (CONFIG[f.kind] ?? []).some((c) => userKeys.includes(c.key) && !c.optional && !f.cfg[c.key]);
  const submit = () => {
    const p = secretPayload(f.src);
    create.mutate({ name: f.name, label: f.label, kind: f.kind, environment: f.environment || null, secret_provider: p.provider, ...(p.value ? { secret_value: p.value } : {}), ...("vault" in p ? { vault: p.vault } : {}), config: Object.fromEntries(Object.entries(f.cfg).filter(([, v]) => v !== "")) },
      { onSuccess: (c) => { setCreated(c); setF((x) => ({ ...x, src: { ...x.src, value: "" } })); setStep(3); } });
  };
  return (
    <>
      <PageHeader title="Connexions" description="Une connexion relie la plate-forme à un système externe. Le secret est chiffré, n'est jamais réaffiché et n'est transmis qu'aux actions autorisées qui le demandent." />
      <section className="adm-card">
        <div className="adm-filters">
          <SearchBox label="Rechercher une connexion" value={search} onChange={setSearch} placeholder="Nom ou identifiant" />
          <div className="field"><label htmlFor="c-kind">Type</label>
            <select id="c-kind" value={kindFilter} onChange={(e) => setKindFilter(e.target.value)}><option value="">Tous les types</option>{[...new Set(all.map((c) => c.kind))].map((k) => <option key={k} value={k}>{KIND[k] ?? k}</option>)}</select></div>
          <span className="lst-count" role="status">{filtered.length} connexion(s)</span>
          <span style={{ flex: 1 }} />
          {manage && <button type="button" className="primary" aria-haspopup="dialog" onClick={() => { reset(); setOpen(true); }}><IconLabel icon="plus">Nouvelle connexion</IconLabel></button>}
        </div>
        <div className="table-wrap"><table className="adm-table"><caption className="sr-only">Connexions de l'espace</caption>
          <thead><tr><SortTh label="Connexion" field="label" sort={sort} onSort={toggle} /><SortTh label="Type" field="kind" sort={sort} onSort={toggle} /><SortTh label="Environnement" field="env" sort={sort} onSort={toggle} /><SortTh label="Dernier test" field="test" sort={sort} onSort={toggle} /><th scope="col">Actions</th></tr></thead>
          <tbody>{paged.slice.map((c) => (
            <tr key={c.id}><td><span className="brand-line"><BrandIcon slug={BRANDS[c.name] ? c.name : c.kind} name={c.label} size={28} /><span><strong>{c.label}</strong> <span className="mono">({c.name})</span></span></span></td><td>{KIND[c.kind]}{c.secret_provider === "vault" && <> <Badge tone="info">Vault</Badge></>}</td><td>{c.environment ? <Badge>{ENV[c.environment]}</Badge> : <Badge tone="muted">Tous</Badge>}</td>
              <td>{c.last_test_ok === null ? <Badge tone="muted">Jamais testée</Badge> : <><Badge tone={c.last_test_ok ? "ok" : "bad"}>{c.last_test_ok ? "✔ Réussi" : "✖ Échec"}</Badge> <span className="lst-sub">{c.last_test_message} ({formatDate(c.last_test_at)})</span></>}</td>
              <td className="row-actions">{manage && <>{c.kind === "oauth2_code" && <button type="button" className="btn-s" onClick={() => authorize.mutate({ id: c.id }, { onSuccess: (r) => { window.location.href = r.authorize_url; } })}>Autoriser</button>} <button type="button" className="btn-s" onClick={() => test.mutate({ id: c.id })} aria-label={`Tester la connexion ${c.label}`}>Tester</button> <Menu label="Plus" ariaLabel={`Plus d'actions pour ${c.label}`} align="end" entries={[{ label: "Supprimer", danger: true, onSelect: () => setToDelete(c) }]} /></>}</td></tr>))}
            {filtered.length === 0 && <EmptyRow cols={5}>{q.isLoading ? "Chargement…" : all.length === 0 ? "Aucune connexion : ajoutez-en une pour relier la plate-forme à un système externe." : "Aucune connexion ne correspond."}</EmptyRow>}</tbody></table></div>
        {paged.pager("Pagination des connexions")}
      </section>
      {toDelete && (
        <Confirm title="Supprimer cette connexion ?" confirmLabel="Supprimer la connexion" busy={del.isPending} onCancel={() => setToDelete(null)} onConfirm={() => { del.mutate({ id: toDelete.id }); setToDelete(null); }}>
          <p>La connexion <strong>{toDelete.label}</strong> et son secret chiffré seront supprimés. Les workflows qui l'utilisent échoueront tant qu'une autre connexion ne la remplace pas.</p>
        </Confirm>
      )}
      {oauthResult && <p role="status">{oauthResult === "ok" ? "✔ Autorisation enregistrée : testez la connexion." : "✖ L'autorisation a échoué ou a été refusée."}</p>}
      {authorize.error && <p role="alert" className="field-error">Erreur : {errorText(authorize.error)}</p>}
      {test.data && <p role="status">{test.data.ok ? "✔" : "✖"} {test.data.message}</p>}
      {manage && open && (
        <Overlay title="Nouvelle connexion" description={`Assistant de configuration — étape ${Math.min(step + 1, 4)} sur 4`} onClose={() => setOpen(false)}>
        <div aria-label="Assistant de configuration" role="group">
          {step === 0 && (
            <fieldset><legend>Quel type de connexion ?</legend>
              <div className="field"><span id="w-tpl-l" className="label">Partir d'un service connu (facultatif)</span>
                <div className="svc-grid" role="group" aria-labelledby="w-tpl-l">
                  {integrations.map((i) => (
                    <button type="button" key={i.slug} className="svc" title={`${i.name} — ${i.category}`} aria-label={`Service ${i.name}`}
                      onClick={() => { setF({ ...f, kind: i.connection.kind, cfg: { ...i.connection.config }, label: i.name, name: i.slug }); setSetup(i.connection.setup); setStep(1); }}>
                      <BrandIcon slug={i.slug} name={i.name} size={30} /><span>{i.name.replace(/ \(.*\)$/, "")}</span>
                    </button>))}
                </div></div>
              {Object.entries(KIND).map(([k, v]) => <div key={k} className="field field-inline"><input type="radio" id={`k-${k}`} name="kind" checked={f.kind === k} onChange={() => setF({ ...f, kind: k })} />{BRANDS[k] && <BrandIcon slug={k} name={v} size={22} />}<label htmlFor={`k-${k}`}>{v}</label></div>)}
              <button type="button" className="primary" onClick={() => setStep(1)}>Continuer</button></fieldset>)}
          {step === 1 && (
            <>
              {setup && <p className="hint">{setup}</p>}
              <div className="field"><label htmlFor="w-l">Nom lisible</label><input id="w-l" value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} /></div>
              <div className="field">
<HelpTip>Utilisé dans les workflows (lettres, chiffres, tirets).</HelpTip><label htmlFor="w-n">Identifiant technique</label><input id="w-n" value={f.name} pattern="[A-Za-z0-9_-]+" onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
              {(CONFIG[f.kind] ?? []).map((c) => (
                <div className="field" key={c.key}><label htmlFor={`w-c-${c.key}`}>{c.label}{c.optional ? " (facultatif)" : ""}</label>
                  {c.options
                    ? <select id={`w-c-${c.key}`} value={f.cfg[c.key] ?? c.options[0]} onChange={(e) => setF({ ...f, cfg: { ...f.cfg, [c.key]: e.target.value } })}>{c.options.map((o) => <option key={o}>{o}</option>)}</select>
                    : <input id={`w-c-${c.key}`} value={f.cfg[c.key] ?? ""} placeholder={c.placeholder} onChange={(e) => setF({ ...f, cfg: { ...f.cfg, [c.key]: e.target.value } })} />}
                </div>))}
              <div className="field"><label htmlFor="w-e">Environnement</label><select id="w-e" value={f.environment} onChange={(e) => setF({ ...f, environment: e.target.value })}><option value="">Tous les environnements</option><option value="test">Recette uniquement</option><option value="prod">Production uniquement</option></select></div>
              <div className="actions"><button type="button" onClick={() => setStep(0)}>Retour</button><button type="button" className="primary" disabled={!f.label || !f.name || (CONFIG[f.kind] ?? []).some((c) => !c.optional && !userKeys.includes(c.key) && !f.cfg[c.key])} onClick={() => setStep(2)}>Continuer</button></div>
            </>)}
          {step === 2 && (
            <>
              <SecretSource value={f.src} onChange={(src) => setF({ ...f, src })} allowDynamic={DYNAMIC_KINDS.includes(f.kind)} />
              {missingUser && <p role="alert" className="field-error">Cette connexion a besoin d'un identifiant : revenez à l'étape précédente, ou choisissez des identifiants dynamiques Vault.</p>}
              {create.error && <p role="alert" className="field-error">Erreur : {errorText(create.error)}</p>}
              <div className="actions"><button type="button" onClick={() => setStep(1)}>Retour</button><button type="button" className="primary" disabled={!secretReady(f.src) || missingUser || create.isPending} onClick={submit}>Enregistrer la connexion</button></div>
            </>)}
          {step === 3 && created && (
            <>
              <p role="status">✔ Connexion « {created.label} » enregistrée. Vous pouvez la tester sans révéler son secret.</p>
              {created.kind === "oauth2_code" && <p className="hint">Déclarez cette adresse de retour chez le fournisseur d'identité : <code>{window.location.origin}/api/v1/oauth/callback/</code>, puis cliquez sur « Autoriser » dans la liste.</p>}
              <div className="actions"><button type="button" onClick={() => test.mutate({ id: created.id })}>Tester maintenant</button><button type="button" onClick={reset}>Ajouter une autre connexion</button></div>
            </>)}
        </div>
        </Overlay>
      )}
    </>
  );
}
