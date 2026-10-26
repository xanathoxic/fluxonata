import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { Action } from "../api";
import { Fields } from "../components/Fields";
import { useApp } from "../context";
import { formatDate } from "../i18n";
import { errorText, useWsMutation, useWsQuery } from "../lib";

interface Ver { version: string; created_at: string; license: string; side_effects: string; idempotent: boolean; network_policy: string; source: string; source_digest: string; changelog: string; runtime: string }
interface Cmp { changed_fields: string[]; source_diff: string; breaking: boolean }
interface Dep { results: { workflow: string; workflow_version: string; action_versions: string[] }[] }

export function ActionLibraryPage() {
  const { slug = "" } = useParams();
  const { can } = useApp();
  const a = useWsQuery<Action>(`action-${slug}`, `/actions/${slug}/`);
  const versions = useWsQuery<{ results: Ver[] }>(`versions-${slug}`, `/actions/${slug}/versions/`);
  const deps = useWsQuery<Dep>(`deps-${slug}`, `/actions/${slug}/dependents/`);
  const deprecate = useWsMutation<{ reason?: string; reinstate?: boolean }>("POST", `/actions/${slug}/deprecate/`, [`action-${slug}`]);
  const rollback = useWsMutation<{ to_version: string }>("POST", `/actions/${slug}/rollback/`, [`versions-${slug}`, `action-${slug}`]);
  const suggest = useWsMutation<{ provider?: string }, { tests: { name: string; inputs: Record<string, unknown> }[] }>("POST", `/actions/${slug}/ai/suggest-tests/`);
  const [pair, setPair] = useState({ a: "", b: "" });
  const [cmp, setCmp] = useState<Cmp | null>(null);
  const [reason, setReason] = useState("");
  if (a.isLoading) return <p role="status">Chargement…</p>;
  if (!a.data) return <p role="alert">Action introuvable.</p>;
  const list = versions.data?.results ?? [];
  return (
    <>
      <nav aria-label="Fil d'Ariane" className="breadcrumb"><Link to="/library">Bibliothèques</Link> / <span aria-current="page">{a.data.name}</span></nav>
      <h1>{a.data.name}</h1>
      <p>{a.data.description}</p>
      {a.data.status !== "deprecated" && <p><Link className="button" to={`/library/actions/${slug}/run`}>Lancer un essai</Link></p>}
      {a.data.status === "deprecated" && <p role="note" className="notice">⚠ Action dépréciée : {a.data.deprecation_reason || "aucun motif indiqué"}.</p>}
      <CompareBox slug={slug} versions={list.map((v) => v.version)} pair={pair} setPair={setPair} cmp={cmp} setCmp={setCmp} />
      <h2>Versions</h2>
      {rollback.error && <p role="alert" className="field-error">Erreur : {errorText(rollback.error)}</p>}
      <div className="table-wrap"><table><caption className="sr-only">Versions de l'action</caption>
        <thead><tr><th scope="col">Version</th><th scope="col">Publiée le</th><th scope="col">Effets</th><th scope="col">Réseau</th><th scope="col">Empreinte</th><th scope="col">Actions</th></tr></thead>
        <tbody>{list.map((v) => (
          <tr key={v.version}><td>{v.version}{v.changelog ? ` — ${v.changelog}` : ""}</td><td>{formatDate(v.created_at)}</td>
            <td>{v.side_effects === "none" ? "Aucun" : `Externe${v.idempotent ? "" : " non idempotent"}`}</td><td>{v.network_policy}</td><td className="mono">{v.source_digest.slice(7, 19)}…</td>
            <td><details><summary>Code</summary><pre tabIndex={0}>{v.source}</pre></details>
              {can("publish") && v !== list[0] && <button type="button" onClick={() => rollback.mutate({ to_version: v.version })} aria-label={`Republier la version ${v.version} comme nouvelle version`}>Revenir à cette version</button>}</td></tr>))}
        </tbody></table></div>
      <h2>Utilisée par</h2>
      {deps.data?.results.length === 0 ? <p>Aucun workflow publié n'utilise cette action.</p> : (
        <ul>{deps.data?.results.map((d) => <li key={`${d.workflow}${d.workflow_version}`}><Link to={`/workflows/${d.workflow}/schema`}>{d.workflow}</Link> v{d.workflow_version} → action {d.action_versions.join(", ")}</li>)}</ul>)}
      {can("publish") && (
        <div className="actions">
          {a.data.status === "deprecated" ? <button type="button" onClick={() => deprecate.mutate({ reinstate: true })}>Réactiver l'action</button> : (
            <form className="inline-form" onSubmit={(e) => { e.preventDefault(); deprecate.mutate({ reason }); }}>
              <div className="field"><label htmlFor="dep-r">Motif de dépréciation</label><input id="dep-r" value={reason} onChange={(e) => setReason(e.target.value)} /></div>
              <button type="submit">Déprécier l'action</button></form>)}
        </div>
      )}
      <h2>Suggestion de tests (assistant IA, facultatif)</h2>
      <button type="button" onClick={() => suggest.mutate({})} disabled={suggest.isPending}>Suggérer des jeux de test</button>
      {suggest.error && <p role="alert" className="field-error">{errorText(suggest.error)}</p>}
      {suggest.data?.tests.map((t) => <div key={t.name} className="notice"><strong>{t.name}</strong><Fields data={t.inputs} /></div>)}
    </>
  );
}

function CompareBox({ slug, versions, pair, setPair, cmp, setCmp }: { slug: string; versions: string[]; pair: { a: string; b: string }; setPair: (p: { a: string; b: string }) => void; cmp: Cmp | null; setCmp: (c: Cmp | null) => void }) {
  const { workspace } = useApp();
  const [err, setErr] = useState("");
  if (versions.length < 2) return null;
  const run = async () => {
    setErr("");
    const r = await fetch(`/api/v1/workspaces/${workspace.slug}/actions/${slug}/compare/?a=${pair.a}&b=${pair.b}`, { credentials: "same-origin" });
    if (r.ok) setCmp((await r.json()) as Cmp); else setErr("Comparaison impossible.");
  };
  return (
    <section aria-label="Comparer deux versions" className="card">
      <h2>Comparer deux versions</h2>
      <div className="inline-form">
        <div className="field"><label htmlFor="c-a">Version A</label><select id="c-a" value={pair.a} onChange={(e) => setPair({ ...pair, a: e.target.value })}><option value="">—</option>{versions.map((v) => <option key={v}>{v}</option>)}</select></div>
        <div className="field"><label htmlFor="c-b">Version B</label><select id="c-b" value={pair.b} onChange={(e) => setPair({ ...pair, b: e.target.value })}><option value="">—</option>{versions.map((v) => <option key={v}>{v}</option>)}</select></div>
        <button type="button" onClick={() => void run()} disabled={!pair.a || !pair.b}>Comparer</button>
      </div>
      {err && <p role="alert" className="field-error">Erreur : {err}</p>}
      {cmp && <><p>{cmp.breaking ? "⚠ Changement de contrat (entrées ou sorties) : les workflows existants peuvent être affectés." : "Contrat d'entrées/sorties inchangé."} Champs modifiés : {cmp.changed_fields.join(", ") || "aucun"}.</p>{cmp.source_diff && <pre tabIndex={0} className="diff">{cmp.source_diff}</pre>}</>}
    </section>
  );
}
