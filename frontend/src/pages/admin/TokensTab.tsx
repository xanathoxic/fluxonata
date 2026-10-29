import { useMemo, useState } from "react";
import { Avatar, Badge, Confirm, EmptyRow, SearchBox, Section, usePaged } from "../../components/AdminUi";
import { Overlay } from "../../components/Overlay";
import { formatDate } from "../../i18n";
import { errorText, useWsMutation, useWsQuery } from "../../lib";
import { Kpi } from "../OpsCharts";

interface SA { results: { id: number; name: string; role: string }[] }
interface Tok { id: number; name: string; prefix: string; service_account: string; scopes: string[]; environments: string[]; expires_at: string; last_used_at: string | null; revoked: boolean }
interface Tokens { results: Tok[] }
const SCOPES: Record<string, string> = { view: "Consulter", run: "Lancer", edit: "Modifier", publish: "Publier" };
const ENVS: Record<string, string> = { dev: "Développement", test: "Recette", prod: "Production" };
const SOON = 14 * 86400000;
const state = (t: Tok): "revoked" | "expired" | "soon" | "ok" =>
  t.revoked ? "revoked" : new Date(t.expires_at).getTime() < Date.now() ? "expired" : new Date(t.expires_at).getTime() - Date.now() < SOON ? "soon" : "ok";

export function TokensTab() {
  const sas = useWsQuery<SA>("sa", "/service-accounts/");
  const tokens = useWsQuery<Tokens>("tokens", "/tokens/");
  const createSa = useWsMutation<{ name: string; role: string }>("POST", "/service-accounts/", ["sa"]);
  const createTok = useWsMutation<Record<string, unknown>, { token: string; notice: string }>("POST", "/tokens/", ["tokens"]);
  const rotate = useWsMutation<{ id: number; overlap_hours: number }, { token: string; notice: string; old_expires_at: string }>("POST", (i) => `/tokens/${i.id}/rotate/`, ["tokens"]);
  const revoke = useWsMutation<{ id: number }>("DELETE", (i) => `/tokens/${i.id}/`, ["tokens"]);
  const [saName, setSaName] = useState("");
  const [openSa, setOpenSa] = useState(false);
  const [openTok, setOpenTok] = useState(false);
  const [toRevoke, setToRevoke] = useState<Tok | null>(null);
  const [form, setForm] = useState({ service_account: "", name: "", days: 30, scopes: ["view", "run"], environments: ["test"] });
  const [search, setSearch] = useState("");
  const [show, setShow] = useState("active");
  const toggle = (key: "scopes" | "environments", v: string) => setForm((f) => ({ ...f, [key]: f[key].includes(v) ? f[key].filter((x) => x !== v) : [...f[key], v] }));
  const all = tokens.data?.results ?? [];
  const rows = useMemo(() => all.filter((t) => (show === "all" || (show === "active" ? !t.revoked : t.revoked)) && `${t.name} ${t.service_account}`.toLowerCase().includes(search.trim().toLowerCase())), [all, show, search]);
  const paged = usePaged(rows, 10);
  const saPaged = usePaged(sas.data?.results ?? [], 6);
  const soon = all.filter((t) => state(t) === "soon").length;
  return (
    <>
      <ul className="kpis" aria-label="Chiffres des jetons">
        <Kpi label="Comptes de service" value={sas.data?.results.length ?? 0} />
        <Kpi label="Jetons actifs" value={all.filter((t) => !t.revoked).length} tone="ok" />
        <Kpi label="Expirent sous 14 jours" value={soon} tone={soon > 0 ? "bad" : undefined} hint={soon > 0 ? "à renouveler" : "aucun"} />
        <Kpi label="Révoqués" value={all.filter((t) => t.revoked).length} />
      </ul>
      <Section title="Comptes de service" hint="Un compte de service est une identité non humaine (intégration, automate) avec un rôle limité dans cet espace."
        actions={<button type="button" onClick={() => { createSa.reset(); setOpenSa(true); }}>Nouveau compte de service</button>}>
        {(sas.data?.results.length ?? 0) === 0 ? <p className="hint">Aucun compte de service : créez-en un pour émettre un jeton d'API.</p> : (
          <ul className="adm-people">{saPaged.slice.map((s) => <li key={s.id}><Avatar name={s.name} /><span><strong>{s.name}</strong><br /><span className="hint">{s.role}</span></span></li>)}</ul>
        )}
        {saPaged.pager("Pagination des comptes de service")}
      </Section>
      <Section title="Jetons d'API" hint="Un jeton donne à un compte de service des droits limités, pour une durée limitée. Sa valeur n'est montrée qu'à la création."
        actions={<button type="button" className="primary" onClick={() => { createTok.reset(); setOpenTok(true); }}>Nouveau jeton</button>}>
        <div className="adm-filters">
          <SearchBox label="Rechercher un jeton" value={search} onChange={setSearch} />
          <div className="field"><label htmlFor="t-show">Afficher</label>
            <select id="t-show" value={show} onChange={(e) => setShow(e.target.value)}><option value="active">Jetons actifs</option><option value="revoked">Jetons révoqués</option><option value="all">Tous</option></select></div>
        </div>
        {rotate.error && <p role="alert" className="field-error">Erreur : {errorText(rotate.error)}</p>}
        {revoke.error && <p role="alert" className="field-error">Erreur : {errorText(revoke.error)}</p>}
        <div className="table-wrap">
          <table className="adm-table">
            <caption className="sr-only">Jetons existants</caption>
            <thead><tr><th scope="col">Nom</th><th scope="col">Préfixe</th><th scope="col">Compte</th><th scope="col">Droits</th><th scope="col">Environnements</th><th scope="col">Expire le</th><th scope="col">Dernier usage</th><th scope="col">Action</th></tr></thead>
            <tbody>
              {paged.slice.map((t) => {
                const st = state(t);
                return (
                  <tr key={t.id}><td>{t.name}</td><td className="mono">{t.prefix}…</td><td>{t.service_account}</td>
                    <td><span className="adm-chips">{t.scopes.map((s) => <Badge key={s} tone="info">{SCOPES[s] ?? s}</Badge>)}</span></td>
                    <td>{t.environments.length ? <span className="adm-chips">{t.environments.map((e) => <Badge key={e}>{ENVS[e] ?? e}</Badge>)}</span> : "tous"}</td>
                    <td>{formatDate(t.expires_at)}{st === "soon" && <><br /><Badge tone="warn">⚠ expire bientôt</Badge></>}{st === "expired" && <><br /><Badge tone="bad">Expiré</Badge></>}</td>
                    <td>{formatDate(t.last_used_at)}</td>
                    <td>{t.revoked ? <Badge tone="bad">Révoqué</Badge> : <><button type="button" onClick={() => rotate.mutate({ id: t.id, overlap_hours: 24 })} aria-label={`Renouveler le jeton ${t.name}`}>Renouveler</button>{" "}<button type="button" className="danger" onClick={() => setToRevoke(t)} aria-label={`Révoquer le jeton ${t.name}`}>Révoquer</button></>}</td></tr>
                );
              })}
              {rows.length === 0 && <EmptyRow cols={8}>{tokens.isLoading ? "Chargement…" : "Aucun jeton ne correspond."}</EmptyRow>}
            </tbody>
          </table>
        </div>
        {paged.pager("Pagination des jetons")}
      </Section>

      {toRevoke && (
        <Confirm title="Révoquer ce jeton ?" confirmLabel="Révoquer le jeton" busy={revoke.isPending} onCancel={() => setToRevoke(null)}
          onConfirm={() => { revoke.mutate({ id: toRevoke.id }); setToRevoke(null); }}>
          <p>Le jeton <strong>{toRevoke.name}</strong> ({toRevoke.service_account}) cessera de fonctionner immédiatement. Cette action est définitive ; pour continuer à l'utiliser, il faudra en créer un nouveau.</p>
        </Confirm>
      )}
      {rotate.data && (
        <Overlay variant="modal" title="Jeton renouvelé" description="La nouvelle valeur n'est affichée qu'une seule fois : copiez-la avant de fermer." onClose={() => rotate.reset()}
          footer={<button type="button" className="primary" onClick={() => rotate.reset()}>J'ai copié le jeton</button>}>
          <div role="alert" className="onetime"><strong>{rotate.data.notice}</strong><br /><code>{rotate.data.token}</code></div>
          <p className="hint">L'ancien jeton reste valable jusqu'au {formatDate(rotate.data.old_expires_at)} (chevauchement de 24 h).</p>
        </Overlay>
      )}
      {openSa && (
        <Overlay variant="modal" title="Nouveau compte de service" onClose={() => setOpenSa(false)}
          footer={<><button type="button" onClick={() => setOpenSa(false)}>Annuler</button><button type="submit" form="sa-form" className="primary">Créer</button></>}>
          <form id="sa-form" onSubmit={(e) => { e.preventDefault(); createSa.mutate({ name: saName, role: "operator" }, { onSuccess: () => { setSaName(""); setOpenSa(false); } }); }}>
            <div className="field"><label htmlFor="sa-n">Nouveau compte de service</label><input id="sa-n" value={saName} pattern="[A-Za-z0-9_-]+" onChange={(e) => setSaName(e.target.value)} required /></div>
            {createSa.error && <p role="alert" className="field-error">Erreur : {errorText(createSa.error)}</p>}
          </form>
        </Overlay>
      )}
      {openTok && (
        <Overlay title="Nouveau jeton d'API" description="Le jeton n'est affiché qu'une seule fois : copiez-le avant de fermer." onClose={() => setOpenTok(false)}
          footer={createTok.data ? <button type="button" className="primary" onClick={() => setOpenTok(false)}>J'ai copié le jeton</button> : <><button type="button" onClick={() => setOpenTok(false)}>Annuler</button><button type="submit" form="tok-form" className="primary" disabled={form.scopes.length === 0}>Créer le jeton</button></>}>
          {createTok.data ? <div role="alert" className="onetime"><strong>{createTok.data.notice}</strong><br /><code>{createTok.data.token}</code></div> : (
            <form id="tok-form" onSubmit={(e) => { e.preventDefault(); createTok.mutate({ ...form, service_account: Number(form.service_account) }); }}>
              <div className="field"><label htmlFor="t-sa">Compte de service</label>
                <select id="t-sa" value={form.service_account} onChange={(e) => setForm({ ...form, service_account: e.target.value })} required>
                  <option value="">Choisir…</option>{sas.data?.results.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></div>
              <div className="field"><label htmlFor="t-n">Nom du jeton</label><input id="t-n" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></div>
              <fieldset><legend>Droits accordés</legend>
                {Object.entries(SCOPES).map(([k, v]) => <div key={k} className="field field-inline"><input type="checkbox" id={`sc-${k}`} checked={form.scopes.includes(k)} onChange={() => toggle("scopes", k)} /><label htmlFor={`sc-${k}`}>{v}</label></div>)}
              </fieldset>
              <fieldset><legend>Environnements autorisés</legend>
                {Object.entries(ENVS).map(([k, v]) => <div key={k} className="field field-inline"><input type="checkbox" id={`en-${k}`} checked={form.environments.includes(k)} onChange={() => toggle("environments", k)} /><label htmlFor={`en-${k}`}>{v}</label></div>)}
              </fieldset>
              <div className="field"><label htmlFor="t-d">Durée de validité (jours, 1 à 365)</label><input id="t-d" type="number" min={1} max={365} value={form.days} onChange={(e) => setForm({ ...form, days: Number(e.target.value) })} /></div>
            </form>
          )}
          {createTok.error && <p role="alert" className="field-error">Erreur : {errorText(createTok.error)}</p>}
        </Overlay>
      )}
    </>
  );
}
