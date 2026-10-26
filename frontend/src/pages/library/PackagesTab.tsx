import { useState } from "react";
import { api, type Action, type ApiError, type Page, type WorkflowSummary } from "../../api";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { errorText, useWsMutation, useWsQuery } from "../../lib";

interface Pkg { slug: string; name: string; version: string; checksum: string; origin: string; created_at: string; download_url: string; contents: { actions: number; workflows: number } }
interface Report { signature?: { status: string; signer: string | null }; package: { name: string; version: string; license: string } | null; items: { kind: string; slug: string; version: string; status: string; messages: string[] }[]; errors: string[]; applied: boolean; can_apply: boolean }
const SIG: Record<string, string> = { trusted: "✔ Signature valide d'un éditeur de confiance", untrusted: "⚠ Signature valide mais éditeur inconnu", invalid: "✖ Signature invalide (paquet modifié ?)", unsigned: "⚠ Paquet non signé" };
const STATUS: Record<string, string> = { new: "✚ Nouveau", identical: "＝ Identique (ignoré)", conflict: "✖ Conflit", invalid: "✖ Invalide" };

export function PackagesTab() {
  const { workspace, can } = useApp();
  const pkgs = useWsQuery<Page<Pkg>>("packages", "/packages/");
  const actions = useWsQuery<Page<Action>>("actions-lib", "/actions/");
  const wfs = useWsQuery<Page<WorkflowSummary>>("workflows", "/workflows/");
  const exp = useWsMutation<Record<string, unknown>>("POST", "/packages/export/", ["packages"]);
  const [f, setF] = useState({ name: "", slug: "", version: "1.0.0", description: "", sign: true });
  const [selA, setSelA] = useState<string[]>([]);
  const [selW, setSelW] = useState<string[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [strategy, setStrategy] = useState("abort");
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const toggle = (list: string[], set: (l: string[]) => void, v: string) => set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const runImport = async (mode: "preview" | "apply") => {
    if (!file) return;
    setBusy(true); setErr("");
    const form = new FormData();
    form.append("file", file); form.append("mode", mode); form.append("strategy", strategy);
    try { setReport(await api<Report>(`/workspaces/${workspace.slug}/packages/import/`, { form })); }
    catch (e) { const a = e as ApiError; setReport((a.details as unknown as Report | null)?.items ? (a.details as unknown as Report) : null); setErr(a.message); }
    finally { setBusy(false); }
  };
  const admin = can("administer");
  if (!can("publish")) return <p>Vous pouvez consulter les paquets existants ; export et import sont réservés aux concepteurs.</p>;
  return (
    <>
      <h2>Paquets</h2>
      <p className="hint">Un paquet regroupe actions et workflows avec un manifeste et des sommes de contrôle. Il ne contient jamais de secret et son import ne lance aucun code.</p>
      <div className="table-wrap"><table><caption className="sr-only">Paquets de l'espace</caption>
        <thead><tr><th scope="col">Paquet</th><th scope="col">Version</th><th scope="col">Contenu</th><th scope="col">Origine</th><th scope="col">Date</th><th scope="col">Téléchargement</th></tr></thead>
        <tbody>{pkgs.data?.results.map((p) => <tr key={`${p.slug}-${p.version}`}><td>{p.name}</td><td>{p.version}</td><td>{p.contents.actions} action(s), {p.contents.workflows} workflow(s)</td><td>{p.origin === "import" ? "Importé" : "Exporté"}</td><td>{formatDate(p.created_at)}</td><td><a href={p.download_url} download>.zip</a></td></tr>)}</tbody></table></div>

      <h3>Exporter</h3>
      <form onSubmit={(e) => { e.preventDefault(); exp.mutate({ ...f, actions: selA.map((s) => ({ slug: s.split("@")[0], version: s.split("@")[1] })), workflows: selW.map((s) => ({ slug: s.split("@")[0], version: s.split("@")[1] })) }); }}>
        <div className="inline-form">
          <div className="field"><label htmlFor="pk-n">Nom</label><input id="pk-n" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></div>
          <div className="field"><label htmlFor="pk-s">Identifiant</label><input id="pk-s" value={f.slug} pattern="[A-Za-z0-9_-]+" onChange={(e) => setF({ ...f, slug: e.target.value })} required /></div>
          <div className="field"><label htmlFor="pk-v">Version</label><input id="pk-v" value={f.version} pattern="\d+\.\d+\.\d+" onChange={(e) => setF({ ...f, version: e.target.value })} required /></div>
        </div>
        <div className="field field-inline"><input id="pk-sign" type="checkbox" checked={f.sign} onChange={(e) => setF({ ...f, sign: e.target.checked })} /><label htmlFor="pk-sign">Signer le paquet (preuve de provenance)</label></div>
        <fieldset><legend>Actions à inclure</legend>
          {actions.data?.results.filter((a) => a.latest_version).map((a) => { const v = `${a.slug}@${a.latest_version?.version}`; return <div key={v} className="field field-inline"><input id={`ea-${v}`} type="checkbox" checked={selA.includes(v)} onChange={() => toggle(selA, setSelA, v)} /><label htmlFor={`ea-${v}`}>{a.name} ({a.latest_version?.version})</label></div>; })}
        </fieldset>
        <fieldset><legend>Workflows à inclure (versions publiées)</legend>
          {wfs.data?.results.filter((w) => w.latest_version).map((w) => { const v = `${w.slug}@${w.latest_version?.version}`; return <div key={v} className="field field-inline"><input id={`ew-${v}`} type="checkbox" checked={selW.includes(v)} onChange={() => toggle(selW, setSelW, v)} /><label htmlFor={`ew-${v}`}>{w.name} ({w.latest_version?.version})</label></div>; })}
        </fieldset>
        <button type="submit" className="primary" disabled={exp.isPending || (!selA.length && !selW.length)}>Créer le paquet</button>
        {exp.isSuccess && <p role="status">Paquet créé : téléchargez-le dans la liste ci-dessus.</p>}
        {exp.error && <p role="alert" className="field-error">Erreur : {errorText(exp.error)}</p>}
      </form>

      {admin && <TrustedKeys />}
      <h3>Importer</h3>
      <div className="field"><label htmlFor="imp-f">Archive de paquet (.zip, 5 Mo maximum)</label><input id="imp-f" type="file" accept=".zip,application/zip" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setReport(null); }} /></div>
      <div className="field"><label htmlFor="imp-s">En cas de conflit</label>
        <select id="imp-s" value={strategy} onChange={(e) => setStrategy(e.target.value)}><option value="abort">Tout annuler</option><option value="skip">Ignorer les éléments en conflit</option></select></div>
      <div className="actions"><button type="button" onClick={() => void runImport("preview")} disabled={!file || busy}>Aperçu (rien n'est modifié)</button>
        <button type="button" className="primary" onClick={() => void runImport("apply")} disabled={!file || busy || (report !== null && !report.can_apply)}>Importer</button></div>
      {err && !report && <p role="alert" className="field-error">Erreur : {err}</p>}
      {report && (
        <section aria-label="Rapport d'import" className="notice">
          <h4>{report.package ? `${report.package.name} ${report.package.version} (licence ${report.package.license})` : "Archive refusée"}</h4>
          {report.signature && <p>{SIG[report.signature.status]}{report.signature.signer ? ` : ${report.signature.signer}` : ""}</p>}
          <p role="status"><strong>{report.applied ? "✔ Import effectué." : report.can_apply ? "✔ Aucun blocage : vous pouvez importer." : "✖ Import bloqué."}</strong></p>
          {report.errors.map((e) => <p key={e} role="alert" className="field-error">Erreur : {e}</p>)}
          <ul>{report.items.map((i) => <li key={`${i.kind}${i.slug}${i.version}`}>{STATUS[i.status]} — {i.kind === "action" ? "action" : "workflow"} {i.slug} {i.version} {i.messages.join(" ")}</li>)}</ul>
        </section>
      )}
    </>
  );
}


function TrustedKeys() {
  const q = useWsQuery<{ instance_public_key: string; instance_key_id: string; results: { id: number; name: string; key_id: string }[] }>("trusted", "/packages/trusted-keys/");
  const add = useWsMutation<{ name: string; public_key: string }>("POST", "/packages/trusted-keys/", ["trusted"]);
  const del = useWsMutation<{ id: number }>("DELETE", (i) => `/packages/trusted-keys/${i.id}/`, ["trusted"]);
  const [k, setK] = useState({ name: "", public_key: "" });
  return (
    <section aria-labelledby="tk">
      <h3 id="tk">Éditeurs de confiance</h3>
      <p className="hint">Clé publique de cette instance (à communiquer aux autres instances pour qu'elles fassent confiance à vos paquets) : <code>{q.data?.instance_public_key}</code></p>
      <ul>{q.data?.results.map((r) => <li key={r.id}>{r.name} <span className="mono">({r.key_id})</span> <button type="button" onClick={() => del.mutate({ id: r.id })} aria-label={`Ne plus faire confiance à ${r.name}`}>Retirer</button></li>)}</ul>
      <form className="inline-form" onSubmit={(e) => { e.preventDefault(); add.mutate(k, { onSuccess: () => setK({ name: "", public_key: "" }) }); }}>
        <div className="field"><label htmlFor="tk-n">Nom de l'éditeur</label><input id="tk-n" value={k.name} onChange={(e) => setK({ ...k, name: e.target.value })} required /></div>
        <div className="field"><label htmlFor="tk-k">Clé publique (base64)</label><input id="tk-k" className="mono" value={k.public_key} onChange={(e) => setK({ ...k, public_key: e.target.value })} required /></div>
        <button type="submit">Faire confiance</button>
      </form>
      {add.error && <p role="alert" className="field-error">Erreur : {errorText(add.error)}</p>}
    </section>
  );
}
