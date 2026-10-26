import { useState } from "react";
import { Link, NavLink, useNavigate, useParams } from "react-router-dom";
import { api, type ApiError } from "../api";
import { FavoriteStar } from "../components/FavoriteStar";
import { useApp } from "../context";
import { errorText, useWsQuery } from "../lib";
import { DraftProvider, useDraft, type SaveState } from "./DraftContext";
import { AuditTab } from "./tabs/AuditTab";
import { FollowTab } from "./tabs/FollowTab";
import { FormTab } from "./tabs/FormTab";
import { IOTab } from "./tabs/IOTab";
import { RunsTab } from "./tabs/RunsTab";
import { SchemaTab } from "./tabs/SchemaTab";
import { SummaryTab } from "./tabs/SummaryTab";
import { VariablesTab } from "./tabs/VariablesTab";
import { VersionsTab } from "./tabs/VersionsTab";

const TABS = [
  { id: "summary", label: "Résumé", el: <SummaryTab /> },
  { id: "variables", label: "Variables", el: <VariablesTab /> },
  { id: "io", label: "Entrées/Sorties", el: <IOTab /> },
  { id: "schema", label: "Schéma", el: <SchemaTab /> },
  { id: "follow", label: "Suivi", el: <FollowTab /> },
  { id: "form", label: "Formulaire d'entrée", el: <FormTab /> },
  { id: "versions", label: "Historique des versions", el: <VersionsTab /> },
  { id: "audit", label: "Audit", el: <AuditTab /> },
  { id: "runs", label: "Exécutions", el: <RunsTab /> },
];
const SAVE_LABEL: Record<SaveState, string> = {
  saved: "✔ Brouillon enregistré", dirty: "● Modifications non enregistrées", saving: "⟳ Enregistrement…",
  conflict: "⚠ Conflit : le brouillon a été modifié ailleurs", error: "⚠ Enregistrement impossible, nouvel essai automatique",
};

interface Dep { actions: { slug: string; name: string | null; pinned: string | null; latest: string | null; deprecated: boolean; exists: boolean }[]; workflows: { slug: string; name: string | null; exists: boolean }[]; connections: string[]; ai_providers: string[] }
interface Usage { results: { workflow: string; workflow_version: string; uses_versions: string[] }[] }

function Header() {
  const d = useDraft();
  const { workspace, can } = useApp();
  const navigate = useNavigate();
  const [panel, setPanel] = useState<"usages" | "deps" | null>(null);
  const [validation, setValidation] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [delError, setDelError] = useState("");
  const deps = useWsQuery<Dep>(`wf-deps-${d.slug}`, `/workflows/${d.slug}/dependencies/`, { enabled: panel === "deps" });
  const usages = useWsQuery<Usage>(`wf-usages-${d.slug}`, `/workflows/${d.slug}/dependents/`, { enabled: panel === "usages" });
  const published = d.wf.latest_version !== null;
  const [inCatalog, setInCatalog] = useState(!!d.wf.in_catalog);
  const [catalogError, setCatalogError] = useState("");
  const toggleCatalog = async () => {
    setCatalogError("");
    try {
      await api(`/workspaces/${workspace.slug}/workflows/${d.slug}/catalog/`, { method: inCatalog ? "DELETE" : "POST" });
      setInCatalog(!inCatalog);
    } catch (e) { setCatalogError(errorText(e as ApiError)); }
  };

  const validate = async () => {
    setValidation("Validation en cours…");
    const issues = await d.validateNow();
    const e = issues.filter((i) => i.level === "error").length;
    setValidation(e ? `✖ ${e} problème(s) bloquant(s) : ouvrez l'onglet Schéma pour les corriger.` : `✔ Le workflow est valide${issues.length ? ` (${issues.length} avertissement(s))` : ""}.`);
  };
  const remove = async () => {
    try { await api(`/workspaces/${workspace.slug}/workflows/${d.slug}/delete/`, { method: "DELETE" }); navigate("/workflows"); }
    catch (e) { setDelError(errorText(e as ApiError)); setConfirmDelete(false); }
  };
  return (
    <header className="wf-header">
      <nav aria-label="Fil d'Ariane" className="breadcrumb">
        <Link to="/workflows">Workflows</Link>{d.wf.folder && ` / ${d.wf.folder.split("/").join(" / ")}`} / <span aria-current="page">{d.wf.name}</span>
      </nav>
      <div className="wf-title">
        <h1>{d.wf.name}</h1>
        <FavoriteStar kind="workflow" slug={d.slug} name={d.wf.name} />
        {d.wf.latest_version && <span className="tag">v{d.wf.latest_version.version}</span>}
        <span role="status" aria-live="polite" className={`save save-${d.saveState}`}>{SAVE_LABEL[d.saveState]}</span>
      </div>
      <div className="action-bar" role="toolbar" aria-label="Actions du workflow">
        {published && can("run") ? <Link className="bar-link bar-primary" to={`/workflows/${d.slug}/run`}>Lancer v{d.wf.latest_version?.version}</Link> : <span className="bar-link disabled" title={published ? "Droit de lancement requis" : "Publiez d'abord une version"} aria-disabled="true">Lancer</span>}
        <button type="button" className="bar-link" onClick={() => void validate()}>Valider</button>
        {can("publish") && <Link className="bar-link bar-publish" to={`/workflows/${d.slug}/versions#publier`}>Publier</Link>}
        {can("publish") && published && <button type="button" className="bar-link" aria-pressed={inCatalog} onClick={() => void toggleCatalog()} title={inCatalog ? "Ce workflow est proposé dans le catalogue d'automatisations" : "Le proposer à toutes les personnes de l'espace dans le catalogue d'automatisations"}>{inCatalog ? "Retirer du catalogue" : "Publier au catalogue"}</button>}
        {can("publish") && <Link className="bar-link" to="/triggers" state={{ workflow: d.slug }}>Planifier</Link>}
        <NavLink className="bar-link" to={`/workflows/${d.slug}/runs`}>Toutes les exécutions</NavLink>
        {can("edit") && <button type="button" className="bar-link bar-danger" onClick={() => setConfirmDelete(true)}>Supprimer</button>}
        <button type="button" className="bar-link" aria-expanded={panel === "usages"} onClick={() => setPanel(panel === "usages" ? null : "usages")}>Rechercher les utilisations</button>
        <button type="button" className="bar-link" aria-expanded={panel === "deps"} onClick={() => setPanel(panel === "deps" ? null : "deps")}>Rechercher les dépendances</button>
      </div>
      {validation && <p role="status" className="notice">{validation}</p>}
      {catalogError && <p role="alert" className="field-error">Erreur : {catalogError}</p>}
      {confirmDelete && <p role="alert" className="notice">Supprimer définitivement ce workflow ? (refusé s'il a déjà été exécuté ou est utilisé ailleurs) <button type="button" className="danger" onClick={() => void remove()}>Oui, supprimer</button> <button type="button" onClick={() => setConfirmDelete(false)}>Non</button></p>}
      {delError && <p role="alert" className="field-error">Erreur : {delError}</p>}
      {panel === "usages" && (
        <section aria-label="Utilisations" className="notice"><h2>Utilisations</h2>
          {usages.data?.results.length === 0 ? <p>Aucun workflow publié n'appelle celui-ci.</p> : <ul>{usages.data?.results.map((u) => <li key={`${u.workflow}${u.workflow_version}`}><Link to={`/workflows/${u.workflow}`}>{u.workflow}</Link> v{u.workflow_version} (utilise v{u.uses_versions.join(", v")})</li>)}</ul>}</section>
      )}
      {panel === "deps" && (
        <section aria-label="Dépendances" className="notice"><h2>Dépendances</h2>
          <ul>
            {deps.data?.actions.map((a) => <li key={a.slug}>Action <Link to={`/library/actions/${a.slug}`}>{a.name ?? a.slug}</Link> {a.exists ? `(version ${a.pinned ?? a.latest ?? "?"}${a.deprecated ? ", dépréciée ⚠" : ""})` : "⚠ introuvable"}</li>)}
            {deps.data?.workflows.map((w) => <li key={w.slug}>Workflow <Link to={`/workflows/${w.slug}`}>{w.name ?? w.slug}</Link>{w.exists ? "" : " ⚠ introuvable"}</li>)}
            {deps.data?.connections.map((c) => <li key={c}>Connexion <Link to="/connections">{c}</Link></li>)}
            {deps.data?.ai_providers.map((p) => <li key={p}>Fournisseur d'IA {p}</li>)}
            {deps.data && !deps.data.actions.length && !deps.data.workflows.length && !deps.data.connections.length && !deps.data.ai_providers.length && <li>Aucune dépendance externe.</li>}
          </ul></section>
      )}
      {d.saveState === "conflict" && (
        <p role="alert" className="conflict">Quelqu'un d'autre a enregistré ce brouillon. <button type="button" onClick={() => void d.reload()}>Recharger la version du serveur</button> <button type="button" onClick={() => void d.save(true)}>Écraser avec ma version</button></p>
      )}
      {d.recover && (
        <p role="alert" className="conflict">Un brouillon local non enregistré a été retrouvé (déconnexion ou fermeture ?). <button type="button" onClick={d.acceptRecovery}>Le récupérer</button> <button type="button" onClick={d.discardRecovery}>L'ignorer</button></p>
      )}
      {d.graph.meta?.ai_generated && <p role="note" className="notice">🤖 Ce brouillon a été généré par IA ({d.graph.meta.provider || "fournisseur inconnu"}). Relisez chaque étape avant de publier : il n'a pas été validé par un humain.</p>}
      {!d.editable && <p role="note" className="hint">Vous avez un accès en lecture seule à ce workflow.</p>}
    </header>
  );
}

function Body() {
  const { tab = "summary" } = useParams();
  const d = useDraft();
  const current = TABS.find((t) => t.id === tab) ?? TABS[0];
  return (
    <div className={`wf-page${current?.id === "schema" ? " wf-fill" : ""}`}>
      <Header />
      <nav aria-label="Sections du workflow" className="wf-tabs">
        <ul>{TABS.map((t) => <li key={t.id}><NavLink to={`/workflows/${d.slug}/${t.id}`} className={({ isActive }) => (isActive || (t.id === current?.id && !tab) ? "active" : "")} aria-current={t.id === current?.id ? "page" : undefined}>{t.label}</NavLink></li>)}</ul>
      </nav>
      <div className={`wf-content tab-${current?.id}`}>{current?.el}</div>
    </div>
  );
}

export function WorkflowPage() {
  const { slug = "" } = useParams();
  return <DraftProvider slug={slug}><Body /></DraftProvider>;
}
