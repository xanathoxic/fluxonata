import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { apiAll, type Integration, type WorkflowSummary } from "../../api";
import { Badge, EmptyRow, SearchBox, usePaged } from "../../components/AdminUi";
import { Overlay } from "../../components/Overlay";
import { RowIcon } from "../../components/RowIcon";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { errorText, useWsMutation, useWsQuery } from "../../lib";
import { JsonField, ParamField } from "../../workflow/HttpConfig";
import { HelpTip } from "../../components/HelpTip";

interface Poll { id: number; name: string; workflow: string; environment: string; interval_seconds: number; mode: string; enabled: boolean; last_poll_at: string | null; last_ok: boolean | null; last_note: string; next_poll_at: string | null }
interface Conn { name: string; label: string; kind: string }
const HTTP_KINDS = ["http_bearer", "api_key", "basic", "oauth2_client", "oauth2_code", "aws_sigv4"];
const MAIL_KINDS = ["imap"];
const SFTP_KINDS = ["sftp"];
const EVERY: [string, number][] = [["Toutes les minutes", 60], ["Toutes les 5 minutes", 300], ["Toutes les 15 minutes", 900], ["Toutes les heures", 3600], ["Une fois par jour", 86400]];
const EMPTY = { kind: "http", folder: "INBOX", from_contains: "", subject_contains: "", name: "", workflow: "", environment: "test", interval_seconds: 300, connection: "", source: "free", integration: "", operation: "", method: "GET", path: "/", items_path: "", id_path: "id", mode: "per_item", fire_existing: false };

/** Polling triggers: start a workflow for each NEW item a web service shows. */
export function PollsTab() {
  const { can, workspace } = useApp();
  const edit = can("publish");
  const wfs = useQuery({ queryKey: ["workflows", workspace.slug], queryFn: () => apiAll<WorkflowSummary>(`/workspaces/${workspace.slug}/workflows/`) });
  const q = useWsQuery<{ results: Poll[] }>("polls", "/poll-triggers/", { refetchInterval: 5000 });
  const [open, setOpen] = useState(false);
  const [f, setF] = useState(EMPTY);
  const allConns = useWsQuery<{ results: Conn[] }>("connections", "/connections/").data?.results ?? [];
  const conns = allConns.filter((c) => (f.kind === "imap" ? MAIL_KINDS : f.kind === "sftp" ? SFTP_KINDS : HTTP_KINDS).includes(c.kind));
  const integrations = useWsQuery<{ results: Integration[] }>("integrations", "/integrations/").data?.results ?? [];
  const add = useWsMutation<Record<string, unknown>, Poll>("POST", "/poll-triggers/", ["polls"]);
  const toggle = useWsMutation<{ id: number; enabled: boolean }>("PATCH", (i) => `/poll-triggers/${i.id}/`, ["polls"]);
  const now = useWsMutation<{ id: number; preview: boolean }>("POST", (i) => `/poll-triggers/${i.id}/poll-now/`, ["polls"]);
  const del = useWsMutation<{ id: number }>("DELETE", (i) => `/poll-triggers/${i.id}/`, ["polls"]);
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [query, setQuery] = useState<Record<string, unknown> | undefined>(undefined);
  const publishable = (wfs.data?.results ?? []).filter((w) => w.latest_version);
  const integ = integrations.find((i) => i.slug === f.integration);
  const op = integ?.operations.find((o) => o.id === f.operation) ?? integ?.operations[0];
  const submit = () => {
    const request = f.kind === "sftp"
      ? { connection: f.connection, path: f.folder, name_contains: f.from_contains }
      : f.kind === "imap"
      ? { connection: f.connection, folder: f.folder, from_contains: f.from_contains, subject_contains: f.subject_contains }
      : f.source === "integration" && integ && op
      ? { connection: f.connection, integration: integ.slug, operation: op.id, params }
      : { connection: f.connection, method: f.method, path: f.path, ...(query ? { query } : {}) };
    add.mutate({ source: f.kind, name: f.name, workflow: f.workflow, environment: f.environment, interval_seconds: f.interval_seconds, request, items_path: f.items_path, id_path: f.id_path, mode: f.mode, fire_existing: f.fire_existing });
  };
  const [search, setSearch] = useState("");
  const rows = (q.data?.results ?? []).filter((r) => `${r.name} ${r.workflow}`.toLowerCase().includes(search.trim().toLowerCase()));
  const paged = usePaged(rows, 10);
  return (
    <>
      <p className="help-row"><HelpTip caption="À savoir">Interroge un service à intervalle régulier (par une connexion) et lance le workflow pour chaque <strong>nouvel</strong> élément, repéré par son identifiant. Le premier relevé mémorise l'existant sans rien lancer (sauf option contraire). Le workflow reçoit l'élément dans l'entrée <code>item</code> (ou la liste dans <code>items</code> en mode lot).</HelpTip></p>
      {edit && <p><button type="button" className="primary" onClick={() => { add.reset(); setF(EMPTY); setParams({}); setQuery(undefined); setOpen(true); }}>Nouveau déclencheur par interrogation</button></p>}
      <section className="adm-card">
      <div className="adm-filters"><SearchBox label="Rechercher un déclencheur" value={search} onChange={setSearch} placeholder="Nom ou workflow" /><span className="lst-count" role="status">{rows.length} déclencheur(s)</span></div>
      <div className="table-wrap"><table className="adm-table"><caption className="sr-only">Déclencheurs par interrogation</caption>
        <thead><tr><th scope="col">Nom</th><th scope="col">Workflow</th><th scope="col">Fréquence</th><th scope="col">Dernier relevé</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>{paged.slice.map((r) => (
          <tr key={r.id}><td>{r.name}{!r.enabled && <> <Badge tone="muted">⏸ Désactivé</Badge></>}</td><td>{r.workflow} ({r.environment})</td><td>{EVERY.find(([, s]) => s === r.interval_seconds)?.[0] ?? `${r.interval_seconds} s`}</td>
            <td>{r.last_poll_at ? <>{r.last_ok ? "✔" : "✖"} {r.last_note}<br /><span className="hint">{formatDate(r.last_poll_at)}</span></> : <span className="hint">{r.last_note || "Pas encore relevé"}</span>}</td>
            <td>{edit && <><button type="button" onClick={() => now.mutate({ id: r.id, preview: true })}>Essayer (sans rien lancer)</button>{" "}<button type="button" onClick={() => toggle.mutate({ id: r.id, enabled: !r.enabled })}>{r.enabled ? "Désactiver" : "Activer"}</button>{" "}<button type="button" className="icon-btn danger-icon" onClick={() => del.mutate({ id: r.id })} aria-label={`Supprimer le déclencheur ${r.name}`} title="Supprimer"><RowIcon name="trash" /></button></>}</td></tr>))}
          {rows.length === 0 && <EmptyRow cols={5}>{q.data?.results.length ? "Aucun déclencheur ne correspond." : "Aucun déclencheur par interrogation."}</EmptyRow>}</tbody></table></div>
      {paged.pager("Pagination des déclencheurs")}
      </section>
      {edit && open && (
        <Overlay title="Nouveau déclencheur par interrogation" description="Quel service interroger, et quoi lancer pour chaque nouvel élément." onClose={() => setOpen(false)}
          footer={add.data ? <button type="button" className="primary" onClick={() => setOpen(false)}>Fermer</button> : <><button type="button" onClick={() => setOpen(false)}>Annuler</button><button type="button" className="primary" onClick={submit} disabled={add.isPending}>Créer</button></>}>
          {add.data ? <p role="status">✔ Déclencheur créé : le premier relevé aura lieu dans quelques secondes.</p> : (
            <div>
              <div className="field"><label htmlFor="pt-n">Nom</label><input id="pt-n" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
              <div className="field"><label htmlFor="pt-w">Workflow à lancer</label><select id="pt-w" value={f.workflow} onChange={(e) => setF({ ...f, workflow: e.target.value })}><option value="">Choisir…</option>{publishable.map((w) => <option key={w.slug} value={w.slug}>{w.name}</option>)}</select></div>
              <div className="field"><label htmlFor="pt-m">Lancement</label><select id="pt-m" value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })}><option value="per_item">Une exécution par nouvel élément (entrée « item »)</option><option value="batch">Une exécution pour tous les nouveaux (entrée « items »)</option></select></div>
              <div className="field"><label htmlFor="pt-i">Fréquence</label><select id="pt-i" value={f.interval_seconds} onChange={(e) => setF({ ...f, interval_seconds: Number(e.target.value) })}>{EVERY.map(([l, s]) => <option key={s} value={s}>{l}</option>)}</select></div>
              <div className="field"><label htmlFor="pt-e">Environnement</label><select id="pt-e" value={f.environment} onChange={(e) => setF({ ...f, environment: e.target.value })}><option value="test">Recette</option><option value="prod">Production</option></select></div>
              <div className="field"><label htmlFor="pt-k">Source</label><select id="pt-k" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value, connection: "", id_path: e.target.value === "imap" || e.target.value === "sftp" ? "id" : f.id_path, folder: e.target.value === "sftp" ? "/" : "INBOX" })}><option value="http">Un service web (par une connexion)</option><option value="imap">Une boîte e-mail (IMAP)</option><option value="sftp">Un dossier SFTP (nouveaux fichiers)</option></select></div>
              <div className="field"><label htmlFor="pt-c">Connexion</label><select id="pt-c" value={f.connection} onChange={(e) => setF({ ...f, connection: e.target.value })}><option value="">Choisir…</option>{conns.map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}</select></div>
              {f.kind === "http" && <div className="field"><label htmlFor="pt-s">Requête</label><select id="pt-s" value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })}><option value="free">Requête libre</option><option value="integration">Opération d'une intégration</option></select></div>}
              {f.kind === "sftp" ? (
                <>
                  <div className="field"><label htmlFor="pt-sf">Dossier à surveiller</label><input id="pt-sf" value={f.folder} placeholder="/depot" onChange={(e) => setF({ ...f, folder: e.target.value })} /></div>
                  <div className="field">
<HelpTip>Chaque nouveau fichier (ou fichier remplacé) lance le workflow, qui reçoit dans <code>item</code> : id, name, path, size, modified. Le contenu se lit ensuite avec une étape « Fichier SFTP ».</HelpTip><label htmlFor="pt-sn">Le nom contient (facultatif)</label><input id="pt-sn" value={f.from_contains} placeholder=".csv" onChange={(e) => setF({ ...f, from_contains: e.target.value })} /></div>
                </>
              ) : f.kind === "imap" ? (
                <>
                  <div className="field"><label htmlFor="pt-fo">Dossier</label><input id="pt-fo" value={f.folder} onChange={(e) => setF({ ...f, folder: e.target.value })} /></div>
                  <div className="field"><label htmlFor="pt-fr">L'expéditeur contient (facultatif)</label><input id="pt-fr" value={f.from_contains} onChange={(e) => setF({ ...f, from_contains: e.target.value })} /></div>
                  <div className="field">
<HelpTip>Messages non lus. Le workflow reçoit dans <code>item</code> : id, from, from_name, to, subject, date, text (20 000 caractères au plus) et attachments (nom, type, taille : les pièces jointes ne sont jamais lues).</HelpTip><label htmlFor="pt-su">L'objet contient (facultatif)</label><input id="pt-su" value={f.subject_contains} onChange={(e) => setF({ ...f, subject_contains: e.target.value })} /></div>
                </>
              ) : f.source === "free" ? (
                <>
                  <div className="field"><label htmlFor="pt-p">Chemin</label><input id="pt-p" value={f.path} onChange={(e) => setF({ ...f, path: e.target.value })} /></div>
                  <JsonField id="pt-q" label="Paramètres de requête (JSON)" value={query} onChange={(v) => setQuery(v as Record<string, unknown> | undefined)} />
                </>
              ) : (
                <>
                  <div className="field"><label htmlFor="pt-g">Service</label><select id="pt-g" value={f.integration} onChange={(e) => { setF({ ...f, integration: e.target.value, operation: "" }); setParams({}); }}><option value="">Choisir…</option>{integrations.map((i) => <option key={i.slug} value={i.slug}>{i.name}</option>)}</select></div>
                  {integ && <div className="field"><label htmlFor="pt-o">Opération</label><select id="pt-o" value={op?.id ?? ""} onChange={(e) => { setF({ ...f, operation: e.target.value }); setParams({}); }}>{integ.operations.filter((o) => o.method === "GET").map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</select></div>}
                  {op?.params.map((p) => <ParamField key={`${op.id}-${p.name}`} prm={p} value={params[p.name]} onChange={(v) => { const n = { ...params }; if (v === undefined) delete n[p.name]; else n[p.name] = v; setParams(n); }} />)}
                </>
              )}
              {f.kind === "http" && <div className="field"><label htmlFor="pt-it">Où se trouve la liste dans la réponse (vide si c'est la réponse elle-même)</label><input id="pt-it" value={f.items_path} placeholder="data.items" onChange={(e) => setF({ ...f, items_path: e.target.value })} /></div>}
              <div className="field"><label htmlFor="pt-id">Identifiant d'un élément</label><input id="pt-id" value={f.id_path} placeholder="id" onChange={(e) => setF({ ...f, id_path: e.target.value })} /></div>
              <div className="field field-inline"><input id="pt-fx" type="checkbox" checked={f.fire_existing} onChange={(e) => setF({ ...f, fire_existing: e.target.checked })} /><label htmlFor="pt-fx">Lancer aussi pour les éléments déjà présents au premier relevé</label></div>
              {add.error && <p role="alert" className="field-error">Erreur : {errorText(add.error)}</p>}
            </div>)}
        </Overlay>
      )}
    </>
  );
}
