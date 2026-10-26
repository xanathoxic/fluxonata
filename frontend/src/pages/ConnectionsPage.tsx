import { useState } from "react";
import { Overlay } from "../components/Overlay";
import { PageHeader } from "../components/PageHeader";
import { RowIcon } from "../components/RowIcon";
import { useApp } from "../context";
import { formatDate } from "../i18n";
import { errorText, useWsMutation, useWsQuery } from "../lib";

interface Conn { id: number; name: string; label: string; kind: string; config: { base_url?: string }; secret: string | null; environment: string | null; last_test_ok: boolean | null; last_test_at: string | null; last_test_message: string }
const KIND: Record<string, string> = { http_bearer: "Service HTTP (jeton)", generic: "Générique (un secret)" };
const ENV: Record<string, string> = { test: "Recette", prod: "Production", dev: "Développement" };

export function ConnectionsPage() {
  const { can } = useApp();
  const q = useWsQuery<{ results: Conn[] }>("connections", "/connections/");
  const create = useWsMutation<Record<string, unknown>, Conn>("POST", "/connections/", ["connections"]);
  const test = useWsMutation<{ id: number }, { ok: boolean; message: string }>("POST", (i) => `/connections/${i.id}/test/`, ["connections"]);
  const del = useWsMutation<{ id: number }>("DELETE", (i) => `/connections/${i.id}/`, ["connections"]);
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [f, setF] = useState({ kind: "generic", label: "", name: "", base_url: "", environment: "", secret_value: "" });
  const [created, setCreated] = useState<Conn | null>(null);
  const manage = can("manage_connections");
  const reset = () => { setStep(0); setCreated(null); setF({ kind: "generic", label: "", name: "", base_url: "", environment: "", secret_value: "" }); };
  const submit = () => create.mutate({ name: f.name, label: f.label, kind: f.kind, environment: f.environment || null, secret_value: f.secret_value, config: f.kind === "http_bearer" ? { base_url: f.base_url } : {} },
    { onSuccess: (c) => { setCreated(c); setF((x) => ({ ...x, secret_value: "" })); setStep(3); } });
  return (
    <>
      <PageHeader title="Connexions" description="Une connexion relie la plate-forme à un système externe. Le secret est chiffré, n'est jamais réaffiché et n'est transmis qu'aux actions autorisées qui le demandent."
        actions={manage && <button type="button" className="primary" onClick={() => { reset(); setOpen(true); }}>Nouvelle connexion</button>} />
      <div className="table-wrap"><table><caption className="sr-only">Connexions de l'espace</caption>
        <thead><tr><th scope="col">Connexion</th><th scope="col">Type</th><th scope="col">Environnement</th><th scope="col">Dernier test</th><th scope="col">Actions</th></tr></thead>
        <tbody>{q.data?.results.map((c) => (
          <tr key={c.id}><td>{c.label} <span className="mono">({c.name})</span></td><td>{KIND[c.kind]}</td><td>{c.environment ? ENV[c.environment] : "Tous"}</td>
            <td>{c.last_test_ok === null ? "Jamais testée" : `${c.last_test_ok ? "✔" : "✖"} ${c.last_test_message} (${formatDate(c.last_test_at)})`}</td>
            <td>{manage && <><button type="button" className="icon-btn" onClick={() => test.mutate({ id: c.id })} aria-label={`Tester la connexion ${c.label}`} title="Tester"><RowIcon name="check" /></button> <button type="button" className="icon-btn danger-icon" onClick={() => del.mutate({ id: c.id })} aria-label={`Supprimer la connexion ${c.label}`} title="Supprimer"><RowIcon name="trash" /></button></>}</td></tr>))}</tbody></table></div>
      {test.data && <p role="status">{test.data.ok ? "✔" : "✖"} {test.data.message}</p>}
      {manage && open && (
        <Overlay title="Nouvelle connexion" description={`Assistant de configuration — étape ${Math.min(step + 1, 4)} sur 4`} onClose={() => setOpen(false)}>
        <div aria-label="Assistant de configuration" role="group">
          {step === 0 && (
            <fieldset><legend>Quel type de connexion ?</legend>
              {Object.entries(KIND).map(([k, v]) => <div key={k} className="field field-inline"><input type="radio" id={`k-${k}`} name="kind" checked={f.kind === k} onChange={() => setF({ ...f, kind: k })} /><label htmlFor={`k-${k}`}>{v}</label></div>)}
              <button type="button" className="primary" onClick={() => setStep(1)}>Continuer</button></fieldset>)}
          {step === 1 && (
            <>
              <div className="field"><label htmlFor="w-l">Nom lisible</label><input id="w-l" value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} /></div>
              <div className="field"><label htmlFor="w-n">Identifiant technique</label><input id="w-n" value={f.name} pattern="[A-Za-z0-9_-]+" onChange={(e) => setF({ ...f, name: e.target.value })} /><p className="hint">Utilisé dans les workflows (lettres, chiffres, tirets).</p></div>
              {f.kind === "http_bearer" && <div className="field"><label htmlFor="w-u">Adresse du service</label><input id="w-u" value={f.base_url} placeholder="https://api.exemple.org/" onChange={(e) => setF({ ...f, base_url: e.target.value })} /><p className="hint">Les adresses internes ou réservées sont refusées (protection SSRF).</p></div>}
              <div className="field"><label htmlFor="w-e">Environnement</label><select id="w-e" value={f.environment} onChange={(e) => setF({ ...f, environment: e.target.value })}><option value="">Tous les environnements</option><option value="test">Recette uniquement</option><option value="prod">Production uniquement</option></select></div>
              <div className="actions"><button type="button" onClick={() => setStep(0)}>Retour</button><button type="button" className="primary" disabled={!f.label || !f.name || (f.kind === "http_bearer" && !f.base_url)} onClick={() => setStep(2)}>Continuer</button></div>
            </>)}
          {step === 2 && (
            <>
              <div className="field"><label htmlFor="w-s">Secret (jeton, mot de passe…)</label><input id="w-s" type="password" autoComplete="off" value={f.secret_value} onChange={(e) => setF({ ...f, secret_value: e.target.value })} /><p className="hint">Il sera chiffré puis ne pourra plus être relu, seulement remplacé.</p></div>
              {create.error && <p role="alert" className="field-error">Erreur : {errorText(create.error)}</p>}
              <div className="actions"><button type="button" onClick={() => setStep(1)}>Retour</button><button type="button" className="primary" disabled={!f.secret_value || create.isPending} onClick={submit}>Enregistrer la connexion</button></div>
            </>)}
          {step === 3 && created && (
            <>
              <p role="status">✔ Connexion « {created.label} » enregistrée. Vous pouvez la tester sans révéler son secret.</p>
              <div className="actions"><button type="button" onClick={() => test.mutate({ id: created.id })}>Tester maintenant</button><button type="button" onClick={reset}>Ajouter une autre connexion</button></div>
            </>)}
        </div>
        </Overlay>
      )}
    </>
  );
}
