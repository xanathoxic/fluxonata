import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import type { ApiError } from "../../api";
import { Badge } from "../../components/AdminUi";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { errorText, useWsMutation, useWsQuery } from "../../lib";
import { useDraft } from "../DraftContext";
import type { Issue } from "../types";
import { HelpTip } from "../../components/HelpTip";

interface Ver { version: string; digest: string; changelog: string; created_at: string }
interface NodeRef { id: string; name: string; type: string }
interface FieldChange { field: string; before: unknown; after: unknown }
interface EdgeRef { from: string; to: string; label: string | null }
interface Cmp { a: string; b: string; identical: boolean; nodes_added: NodeRef[]; nodes_removed: NodeRef[]; nodes_changed: (NodeRef & { fields: FieldChange[] })[]; edges_added: EdgeRef[]; edges_removed: EdgeRef[]; sections: Record<string, { before: unknown; after: unknown }> }
const show = (v: unknown) => (v === undefined || v === null ? "—" : typeof v === "string" ? v : JSON.stringify(v));
const edgeText = (e: EdgeRef) => `${e.from} → ${e.to}${e.label ? ` (${e.label})` : ""}`;

export function VersionsTab() {
  const d = useDraft();
  const { workspace, can } = useApp();
  const q = useWsQuery<{ results: Ver[] }>(`wf-versions-${d.slug}`, `/workflows/${d.slug}/versions/`);
  const publish = useWsMutation<{ bump: string; changelog: string }, { version: string }>("POST", `/workflows/${d.slug}/publish/`, [`wf-versions-${d.slug}`, "workflows"]);
  const rollback = useWsMutation<{ to_version: string }>("POST", `/workflows/${d.slug}/rollback/`, [`wf-versions-${d.slug}`]);
  const [bump, setBump] = useState("minor");
  const [changelog, setChangelog] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [pair, setPair] = useState({ a: "", b: "" });
  const [cmp, setCmp] = useState<Cmp | null>(null);
  const errors = d.issues.filter((i) => i.level === "error").length;
  const { hash } = useLocation();
  useEffect(() => { if (hash === "#publier") { const el = document.getElementById("bump"); el?.scrollIntoView({ block: "center" }); el?.focus(); } }, [hash, q.isSuccess]);

  const doPublish = async () => {
    setMsg(null);
    if (d.saveState !== "saved") await d.save();
    publish.mutate({ bump, changelog }, {
      onSuccess: (v) => { setMsg(`Version ${v.version} publiée. Elle est désormais lançable.`); setChangelog(""); void d.refetchWorkflow(); },
      onError: (e: ApiError) => { const list = (e.details as { issues?: Issue[] } | null)?.issues; if (list) d.setServerIssues(list); setMsg(list ? "Publication impossible : corrigez les problèmes listés dans l'onglet Schéma." : e.message); },
    });
  };
  const compare = async () => {
    const r = await fetch(`/api/v1/workspaces/${workspace.slug}/workflows/${d.slug}/versions/diff/?a=${pair.a}&b=${pair.b}`, { credentials: "same-origin" });
    setCmp(r.ok ? ((await r.json()) as Cmp) : null);
  };
  const list = q.data?.results ?? [];
  return (
    <section aria-label="Historique des versions">
      <h2>Historique des versions</h2>
      <div className="ver-grid">
      <div className="ver-main">
      <p className="help-row"><HelpTip caption="À savoir">Une version publiée est immuable : « revenir à une version » en publie une nouvelle copie, l'historique n'est jamais réécrit.</HelpTip></p>
      <div className="table-wrap">
        <table>
          <caption className="sr-only">Versions publiées</caption>
          <thead><tr><th scope="col">Version</th><th scope="col">Publiée le</th><th scope="col">Note</th><th scope="col">Empreinte</th><th scope="col">Action</th></tr></thead>
          <tbody>
            {list.map((v, i) => (
              <tr key={v.version}><td><Badge tone={i === 0 ? "ok" : "muted"}>{v.version}</Badge>{i === 0 ? " dernière" : ""}</td><td>{formatDate(v.created_at)}</td><td>{v.changelog || "—"}</td><td className="mono">{v.digest.slice(7, 19)}…</td>
                <td>{can("publish") && i > 0 && <button type="button" onClick={() => rollback.mutate({ to_version: v.version })} aria-label={`Republier la version ${v.version} comme nouvelle version`}>Revenir à cette version</button>}</td></tr>
            ))}
            {list.length === 0 && <tr><td colSpan={5} className="hint">Jamais publié.</td></tr>}
          </tbody>
        </table>
      </div>
      {rollback.error && <p role="alert" className="field-error">Erreur : {errorText(rollback.error)}</p>}
      {list.length > 1 && (
        <div className="inline-form">
          <div className="field"><label htmlFor="wc-a">Comparer la version</label><select id="wc-a" value={pair.a} onChange={(e) => setPair({ ...pair, a: e.target.value })}><option value="">—</option><option value="draft">Brouillon actuel</option>{list.map((v) => <option key={v.version}>{v.version}</option>)}</select></div>
          <div className="field"><label htmlFor="wc-b">avec la version</label><select id="wc-b" value={pair.b} onChange={(e) => setPair({ ...pair, b: e.target.value })}><option value="">—</option><option value="draft">Brouillon actuel</option>{list.map((v) => <option key={v.version}>{v.version}</option>)}</select></div>
          <button type="button" onClick={() => void compare()} disabled={!pair.a || !pair.b}>Comparer</button>
        </div>
      )}
      {cmp && (
        <div role="status" className="notice">
          <h3>{cmp.a === "draft" ? "Brouillon" : `Version ${cmp.a}`} → {cmp.b === "draft" ? "Brouillon" : `Version ${cmp.b}`}</h3>
          {cmp.identical ? <p>Aucune différence de comportement (la disposition sur le schéma n'est pas comparée).</p> : (
            <>
              {cmp.nodes_added.length > 0 && <p><strong>Étapes ajoutées :</strong> {cmp.nodes_added.map((n) => `${n.name} (${n.id})`).join(", ")}</p>}
              {cmp.nodes_removed.length > 0 && <p><strong>Étapes retirées :</strong> {cmp.nodes_removed.map((n) => `${n.name} (${n.id})`).join(", ")}</p>}
              {cmp.nodes_changed.map((n) => (
                <details key={n.id}><summary>Étape modifiée : {n.name} ({n.id}) — {n.fields.length} champ(s)</summary>
                  <ul>{n.fields.map((f) => <li key={f.field}><code>{f.field}</code> : <span className="mono">{show(f.before)}</span> → <span className="mono">{show(f.after)}</span></li>)}</ul></details>))}
              {cmp.edges_added.length > 0 && <p><strong>Liaisons ajoutées :</strong> {cmp.edges_added.map(edgeText).join(" ; ")}</p>}
              {cmp.edges_removed.length > 0 && <p><strong>Liaisons retirées :</strong> {cmp.edges_removed.map(edgeText).join(" ; ")}</p>}
              {Object.entries(cmp.sections).map(([k, v]) => <details key={k}><summary>{({ inputs: "Entrées", outputs: "Sorties", variables: "Variables", form: "Formulaire" } as Record<string, string>)[k] ?? k} modifiées</summary><p className="mono">{show(v.before)}</p><p className="mono">→ {show(v.after)}</p></details>)}
            </>
          )}
        </div>
      )}
      </div>
      {can("publish") && (
        <aside className="adm-card ver-publish" id="publier" aria-label="Publier une version">
          <h3>Publier</h3>
          <div className="publish">
            <div className="field"><label htmlFor="bump">Type de version</label><select id="bump" value={bump} onChange={(e) => setBump(e.target.value)}><option value="patch">Correctif</option><option value="minor">Évolution</option><option value="major">Changement majeur</option></select></div>
            <div className="field"><label htmlFor="chg">Note de version</label><input id="chg" value={changelog} maxLength={300} onChange={(e) => setChangelog(e.target.value)} /></div>
            <button type="button" className="primary" onClick={() => void doPublish()} disabled={errors > 0 || d.graph.nodes.length === 0} aria-describedby={errors > 0 ? "pub-why" : undefined}>Publier une nouvelle version</button>
          </div>
          {errors > 0 && <p id="pub-why" className="hint">Corrigez les {errors} problème(s) bloquant(s) (onglet Schéma) pour publier.</p>}
          {msg && <p role="status">{msg}</p>}
        </aside>
      )}
      </div>
    </section>
  );
}
