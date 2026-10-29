import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import type { Action } from "../api";
import { ContractView } from "../components/ContractView";
import { TestCasesSection } from "./library/TestCases";
import { Section } from "../components/AdminUi";
import { DiffView } from "../components/DiffView";
import { SourceViewer } from "../components/SourceViewer";
import { Tabs } from "../components/Tabs";
import { useApp } from "../context";
import { formatDate } from "../i18n";
import { errorText, useWsMutation, useWsQuery } from "../lib";
import { TagInput, TagList } from "../components/Tags";

interface Ver { version: string; created_at: string; license: string; side_effects: string; idempotent: boolean; network_policy: string; source: string; source_digest: string; changelog: string; runtime: string }
interface Cmp { changed_fields: string[]; source_diff: string; breaking: boolean }
interface Dep { results: { workflow: string; workflow_version: string; action_versions: string[] }[] }

export function ActionLibraryPage() {
  const { slug = "" } = useParams();
  const { can } = useApp();
  const a = useWsQuery<Action>(`action-${slug}`, `/actions/${slug}/`);
  const versions = useWsQuery<{ results: Ver[] }>(`versions-${slug}`, `/actions/${slug}/versions/`);
  const deps = useWsQuery<Dep>(`deps-${slug}`, `/actions/${slug}/dependents/`);
  const known = useWsQuery<{ results: { tags?: string[] }[] }>("actions-tags", "/actions/");
  const deprecate = useWsMutation<{ reason?: string; reinstate?: boolean }>("POST", `/actions/${slug}/deprecate/`, [`action-${slug}`]);
  const rollback = useWsMutation<{ to_version: string }>("POST", `/actions/${slug}/rollback/`, [`versions-${slug}`, `action-${slug}`]);
  const [pair, setPair] = useState({ a: "", b: "" });
  const [cmp, setCmp] = useState<Cmp | null>(null);
  const [reason, setReason] = useState("");
  const [params, setParams] = useSearchParams();
  const [shown, setShown] = useState("");
  if (a.isLoading) return <p role="status">Chargement…</p>;
  if (!a.data) return <p role="alert">Action introuvable.</p>;
  const list = versions.data?.results ?? [];
  const tab = ["contrat", "code", "versions", "usages", "gestion"].includes(params.get("onglet") ?? "") ? (params.get("onglet") as string) : "contrat";
  const setTab = (id: string) => setParams((p) => { const n = new URLSearchParams(p); n.set("onglet", id); return n; }, { replace: true });
  const current = list.find((v) => v.version === shown) ?? list[0];
  const contract = a.data.latest_version?.contract;
  const tabs = [
    { id: "contrat", label: "Contrat", content: contract ? <><p className="hint">Contrat de la version {a.data.latest_version?.version}, la version publiée la plus récente.</p><ContractView contract={contract} /></> : <p>Cette action n'a pas de contrat publié.</p> },
    { id: "code", label: "Code", content: !current ? <p>Aucune version publiée.</p> : (
      <>
        <div className="field src-pick"><label htmlFor="src-v">Version affichée</label>
          <select id="src-v" value={current.version} onChange={(e) => setShown(e.target.value)}>{list.map((v) => <option key={v.version} value={v.version}>{v.version}{v === list[0] ? " (dernière)" : ""}</option>)}</select></div>
        <SourceViewer source={current.source} runtime={current.runtime} version={current.version} digest={current.source_digest} name={a.data.slug} />
      </>) },
    { id: "versions", label: `Versions (${list.length})`, content: (
      <>
        <CompareBox slug={slug} versions={list.map((v) => v.version)} pair={pair} setPair={setPair} cmp={cmp} setCmp={setCmp} />
        {rollback.error && <p role="alert" className="field-error">Erreur : {errorText(rollback.error)}</p>}
      <div className="table-wrap"><table><caption className="sr-only">Versions de l'action</caption>
        <thead><tr><th scope="col">Version</th><th scope="col">Publiée le</th><th scope="col">Effets</th><th scope="col">Réseau</th><th scope="col">Empreinte</th><th scope="col">Actions</th></tr></thead>
        <tbody>{list.map((v) => (
          <tr key={v.version}><td>{v.version}{v.changelog ? ` — ${v.changelog}` : ""}</td><td>{formatDate(v.created_at)}</td>
            <td>{v.side_effects === "none" ? "Aucun" : `Externe${v.idempotent ? "" : " non idempotent"}`}</td><td>{v.network_policy}</td><td className="mono">{v.source_digest.slice(7, 19)}…</td>
            <td><button type="button" onClick={() => { setShown(v.version); setTab("code"); }} aria-label={`Voir le code de la version ${v.version}`}>Voir le code</button>
              {can("publish") && v !== list[0] && <button type="button" onClick={() => rollback.mutate({ to_version: v.version })} aria-label={`Revenir à cette version (${v.version}) : la republier comme nouvelle version`}>Revenir à cette version</button>}</td></tr>))}
        </tbody></table></div>
      </>) },
    { id: "usages", label: `Utilisée par${deps.data ? ` (${deps.data.results.length})` : ""}`, content: (
      <>
      <Section title="Workflows publiés qui utilisent cette action" hint="Avant de changer le contrat d'une action, vérifiez ici quels workflows seraient affectés.">
        {deps.data?.results.length === 0 ? <p className="hint">Aucun workflow publié n'utilise cette action.</p> : (
          <div className="table-wrap"><table><caption className="sr-only">Workflows utilisant l'action</caption>
            <thead><tr><th scope="col">Workflow</th><th scope="col">Version du workflow</th><th scope="col">Version de l'action</th></tr></thead>
            <tbody>{deps.data?.results.map((d) => <tr key={`${d.workflow}${d.workflow_version}`}><td><Link to={`/workflows/${d.workflow}/schema`}>{d.workflow}</Link></td><td>v{d.workflow_version}</td><td>{d.action_versions.join(", ")}</td></tr>)}</tbody></table></div>)}
      </Section>
      </>) },
    ...(can("publish") ? [{ id: "gestion", label: "Gestion", content: (
      <>
        <Section title="Cycle de vie" info={a.data.status === "deprecated" ? `Action dépréciée : ${a.data.deprecation_reason || "aucun motif indiqué"}.` : "Une action dépréciée reste utilisable par les workflows existants, mais n'est plus proposée pour les nouveaux."}
          hint="Le motif est affiché sur la fiche et dans le catalogue ; vous pouvez réactiver l'action à tout moment.">
          {a.data.status === "deprecated" ? (
            <div className="actions"><button type="button" onClick={() => deprecate.mutate({ reinstate: true })}>Réactiver l'action</button></div>
          ) : (
            <form className="inline-form" onSubmit={(e) => { e.preventDefault(); deprecate.mutate({ reason }); }}>
              <div className="field grow"><label htmlFor="dep-r">Motif de dépréciation</label><input id="dep-r" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Remplacée par une autre action, obsolète…" /></div>
              <button type="submit" className="danger">Déprécier l'action</button>
            </form>)}
          {deprecate.error && <p role="alert" className="field-error">Erreur : {errorText(deprecate.error)}</p>}
        </Section>
        {a.data.latest_version && <TestCasesSection slug={slug} schema={a.data.latest_version.input_schema} />}
      </>) }] : []),
  ];
  return (
    <>
      <nav aria-label="Fil d'Ariane" className="breadcrumb"><Link to="/library">Bibliothèques</Link> / <span aria-current="page">{a.data.name}</span></nav>
      <h1>{a.data.name}</h1>
      <p>{a.data.description}</p>
      <ActionTags slug={slug} tags={a.data.tags ?? []} editable={can("publish")} suggestions={[...new Set((known.data?.results ?? []).flatMap((x) => x.tags ?? []))].sort()} />
      {a.data.status !== "deprecated" && <p><Link className="button" to={`/library/actions/${slug}/run`}>Lancer un essai</Link></p>}
      {a.data.status === "deprecated" && <p role="note" className="notice">⚠ Action dépréciée : {a.data.deprecation_reason || "aucun motif indiqué"}.</p>}
      <div className="action-tabs"><Tabs label="Sections de l'action" active={tab} onChange={setTab} tabs={tabs} /></div>
    </>
  );
}

function ActionTags({ slug, tags, editable, suggestions }: { slug: string; tags: string[]; editable: boolean; suggestions: string[] }) {
  const save = useWsMutation<{ tags: string[] }>("PATCH", `/actions/${slug}/meta/`, [`action-${slug}`, "actions-lib", "actions"]);
  const [draft, setDraft] = useState<string[] | null>(null);
  const value = draft ?? tags;
  if (!editable) return tags.length ? <p><TagList tags={tags} /></p> : null;
  return (
    <section className="adm-card tag-card" aria-label="Étiquettes de l'action">
      <TagInput value={value} onChange={setDraft} suggestions={suggestions} />
      {draft && <div className="adm-savebar"><button type="button" className="primary" disabled={save.isPending} onClick={() => save.mutate({ tags: draft }, { onSuccess: () => setDraft(null) })}>Enregistrer les étiquettes</button><button type="button" onClick={() => setDraft(null)}>Annuler</button>{save.error && <span role="alert" className="field-error">Erreur : {errorText(save.error)}</span>}</div>}
    </section>
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
      {cmp && <><p>{cmp.breaking ? "⚠ Changement de contrat (entrées ou sorties) : les workflows existants peuvent être affectés." : "Contrat d'entrées/sorties inchangé."} Champs modifiés : {cmp.changed_fields.join(", ") || "aucun"}.</p>{cmp.source_diff ? <DiffView diff={cmp.source_diff} from={pair.a} to={pair.b} /> : <p className="hint">Code identique.</p>}</>}
    </section>
  );
}
