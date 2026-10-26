import { useState } from "react";
import { Overlay } from "../../components/Overlay";
import { formatDate } from "../../i18n";
import { errorText, useWsMutation, useWsQuery } from "../../lib";

interface SA { results: { id: number; name: string; role: string }[] }
interface Tokens { results: { id: number; name: string; prefix: string; service_account: string; scopes: string[]; environments: string[]; expires_at: string; last_used_at: string | null; revoked: boolean }[] }
const SCOPES: Record<string, string> = { view: "Consulter", run: "Lancer", edit: "Modifier", publish: "Publier" };

export function TokensTab() {
  const sas = useWsQuery<SA>("sa", "/service-accounts/");
  const tokens = useWsQuery<Tokens>("tokens", "/tokens/");
  const createSa = useWsMutation<{ name: string; role: string }>("POST", "/service-accounts/", ["sa"]);
  const createTok = useWsMutation<Record<string, unknown>, { token: string; notice: string }>("POST", "/tokens/", ["tokens"]);
  const revoke = useWsMutation<{ id: number }>("DELETE", (i) => `/tokens/${i.id}/`, ["tokens"]);
  const [saName, setSaName] = useState("");
  const [openSa, setOpenSa] = useState(false);
  const [openTok, setOpenTok] = useState(false);
  const [form, setForm] = useState({ service_account: "", name: "", days: 30, scopes: ["view", "run"], environments: ["test"] });
  const toggle = (key: "scopes" | "environments", v: string) => setForm((f) => ({ ...f, [key]: f[key].includes(v) ? f[key].filter((x) => x !== v) : [...f[key], v] }));
  return (
    <>
      <h2>Comptes de service</h2>
      <p className="hint">Un compte de service est une identité non humaine (intégration, automate) avec un rôle limité dans cet espace.</p>
      <ul>{sas.data?.results.map((s) => <li key={s.id}>{s.name} — {s.role}</li>)}</ul>
      <button type="button" onClick={() => { createSa.reset(); setOpenSa(true); }}>Nouveau compte de service</button>
      {openSa && (
        <Overlay variant="modal" title="Nouveau compte de service" onClose={() => setOpenSa(false)}
          footer={<><button type="button" onClick={() => setOpenSa(false)}>Annuler</button><button type="submit" form="sa-form" className="primary">Créer</button></>}>
          <form id="sa-form" onSubmit={(e) => { e.preventDefault(); createSa.mutate({ name: saName, role: "operator" }, { onSuccess: () => { setSaName(""); setOpenSa(false); } }); }}>
            <div className="field"><label htmlFor="sa-n">Nouveau compte de service</label><input id="sa-n" value={saName} pattern="[A-Za-z0-9_-]+" onChange={(e) => setSaName(e.target.value)} required /></div>
            {createSa.error && <p role="alert" className="field-error">Erreur : {errorText(createSa.error)}</p>}
          </form>
        </Overlay>
      )}

      <h2>Jetons d'API</h2>
      <button type="button" className="primary" onClick={() => { createTok.reset(); setOpenTok(true); }}>Nouveau jeton</button>
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
          {[["dev", "Développement"], ["test", "Recette"], ["prod", "Production"]].map(([k, v]) => <div key={k} className="field field-inline"><input type="checkbox" id={`en-${k}`} checked={form.environments.includes(k as string)} onChange={() => toggle("environments", k as string)} /><label htmlFor={`en-${k}`}>{v}</label></div>)}
        </fieldset>
        <div className="field"><label htmlFor="t-d">Durée de validité (jours, 1 à 365)</label><input id="t-d" type="number" min={1} max={365} value={form.days} onChange={(e) => setForm({ ...form, days: Number(e.target.value) })} /></div>
      </form>
          )}
          {createTok.error && <p role="alert" className="field-error">Erreur : {errorText(createTok.error)}</p>}
        </Overlay>
      )}
      <div className="table-wrap">
        <table>
          <caption className="sr-only">Jetons existants</caption>
          <thead><tr><th scope="col">Nom</th><th scope="col">Préfixe</th><th scope="col">Compte</th><th scope="col">Droits</th><th scope="col">Environnements</th><th scope="col">Expire le</th><th scope="col">Dernier usage</th><th scope="col">Action</th></tr></thead>
          <tbody>
            {tokens.data?.results.map((t) => (
              <tr key={t.id}><td>{t.name}</td><td className="mono">{t.prefix}…</td><td>{t.service_account}</td><td>{t.scopes.join(", ")}</td><td>{t.environments.join(", ") || "tous"}</td>
                <td>{formatDate(t.expires_at)}</td><td>{formatDate(t.last_used_at)}</td>
                <td>{t.revoked ? "Révoqué" : <button type="button" className="danger" onClick={() => revoke.mutate({ id: t.id })} aria-label={`Révoquer le jeton ${t.name}`}>Révoquer</button>}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
