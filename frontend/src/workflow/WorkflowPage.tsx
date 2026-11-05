import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ApiError } from "../api";
import { Confirm } from "../components/AdminUi";
import { EditorShell } from "../components/EditorShell";
import { FavoriteStar } from "../components/FavoriteStar";
import { Menu, type MenuEntry } from "../components/Menu";
import { Overlay } from "../components/Overlay";
import { PageTabs } from "../components/PageTabs";
import { StatusLine } from "../components/StatusLine";
import { useApp } from "../context";
import { errorText, useWsQuery } from "../lib";
import { DraftProvider, useDraft } from "./DraftContext";
import { PublishDialog } from "./PublishDialog";
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
  { id: "summary", label: "Informations", el: <SummaryTab /> },
  { id: "variables", label: "Variables", el: <VariablesTab /> },
  { id: "io", label: "Entrées/Sorties", el: <IOTab /> },
  { id: "schema", label: "Conception", el: <SchemaTab /> },
  { id: "follow", label: "Suivi", el: <FollowTab /> },
  { id: "form", label: "Formulaire d'entrée", el: <FormTab /> },
  { id: "versions", label: "Versions", el: <VersionsTab /> },
  { id: "audit", label: "Activité", el: <AuditTab /> },
  { id: "runs", label: "Exécutions", el: <RunsTab /> },
];
interface Dep { actions: { slug: string; name: string | null; pinned: string | null; latest: string | null; deprecated: boolean; exists: boolean }[]; workflows: { slug: string; name: string | null; exists: boolean }[]; connections: string[]; ai_providers: string[] }
interface Usage { results: { workflow: string; workflow_version: string; uses_versions: string[] }[] }

/** The pieces of the editor header (status line, actions, notices, dialogs), built once for every tab. */
function useHeaderParts() {
  const d = useDraft();
  const { workspace, can } = useApp();
  const navigate = useNavigate();
  const [panel, setPanel] = useState<"usages" | "deps" | null>(null);
  const [validation, setValidation] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [publishing, setPublishing] = useState(false);
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

  const moreEntries: MenuEntry[] = [
    { kind: "heading", label: "Diffusion" },
    ...(can("publish") && published ? [{ label: inCatalog ? "Retirer du catalogue" : "Publier au catalogue", hint: inCatalog ? "Proposé à toutes les personnes de l'espace" : "Le proposer dans le catalogue d'automatisations", onSelect: () => void toggleCatalog() }] : []),
    ...(can("publish") ? [{ label: "Planifier", hint: "Lancer automatiquement (horaire, webhook)", onSelect: () => navigate("/triggers", { state: { workflow: d.slug } }) }] : []),
    { kind: "separator" },
    { kind: "heading", label: "Suivi et analyse" },
    { label: "Toutes les exécutions", onSelect: () => navigate(`/workflows/${d.slug}/runs`) },
    { label: "Rechercher les utilisations", hint: "Workflows qui appellent celui-ci", onSelect: () => setPanel("usages") },
    { label: "Rechercher les dépendances", hint: "Actions, connexions, fournisseurs d'IA", onSelect: () => setPanel("deps") },
    ...(can("edit") && d.editable ? [{ kind: "separator" } as MenuEntry, { label: "Supprimer", danger: true, onSelect: () => setConfirmDelete(true) }] : []),
  ];
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
  const status = <StatusLine published={d.wf.latest_version ? `v${d.wf.latest_version.version} publiée` : "Jamais publié"} state={d.saveState} />;
  const actions = (
    <>
      {published && can("run") && d.wf.folder_level !== "view" ? <Link className="bar-link bar-primary" to={`/workflows/${d.slug}/run`}>Lancer v{d.wf.latest_version?.version}</Link> : <span className="bar-link disabled" title={published ? "Droit de lancement requis" : "Publiez d'abord une version"} aria-disabled="true">Lancer</span>}
      <button type="button" className="bar-link" onClick={() => void validate()}>Valider</button>
      {can("publish") && d.editable && <button type="button" className="bar-link bar-publish" aria-haspopup="dialog" onClick={() => setPublishing(true)}>Publier</button>}
      <Menu label="Plus" ariaLabel="Plus d'actions" align="end" className="bar-link" entries={moreEntries} />
    </>
  );
  const notices = (
    <>
      {validation && <p role="status" className="notice">{validation}</p>}
      {catalogError && <p role="alert" className="field-error">Erreur : {catalogError}</p>}
      {delError && <p role="alert" className="field-error">Erreur : {delError}</p>}
      {d.saveState === "conflict" && (
        <p role="alert" className="conflict">Quelqu'un d'autre a enregistré ce brouillon. <button type="button" onClick={() => void d.reload()}>Recharger la version du serveur</button> <button type="button" onClick={() => void d.save(true)}>Écraser avec ma version</button></p>
      )}
      {d.recover && (
        <p role="alert" className="conflict">Un brouillon local non enregistré a été retrouvé (déconnexion ou fermeture ?). <button type="button" onClick={d.acceptRecovery}>Le récupérer</button> <button type="button" onClick={d.discardRecovery}>L'ignorer</button></p>
      )}
      {d.graph.meta?.ai_generated && <p role="note" className="notice">🤖 Ce brouillon a été généré par IA ({d.graph.meta.provider || "fournisseur inconnu"}). Relisez chaque étape avant de publier : il n'a pas été validé par un humain.</p>}
      {!d.editable && <p role="note" className="hint">Vous avez un accès en lecture seule à ce workflow.</p>}
    </>
  );
  const dialogs = (
    <>
      {publishing && <PublishDialog onClose={() => setPublishing(false)} />}
      {confirmDelete && (
        <Confirm title={`Supprimer « ${d.wf.name} » ?`} confirmLabel="Supprimer" onCancel={() => setConfirmDelete(false)} onConfirm={() => void remove()}>
          <p>Le workflow <strong>{d.wf.name}</strong> sera supprimé définitivement. L'opération est refusée s'il a déjà été exécuté ou s'il est utilisé ailleurs : dans ce cas, déclarez-le déprécié plutôt que de le supprimer.</p>
        </Confirm>
      )}
      {panel === "usages" && (
        <Overlay title="Utilisations" description="Les workflows publiés qui appellent celui-ci." onClose={() => setPanel(null)} footer={<button type="button" onClick={() => setPanel(null)}>Fermer</button>}>
          {usages.data?.results.length === 0 ? <p>Aucun workflow publié n'appelle celui-ci.</p> : <ul>{usages.data?.results.map((u) => <li key={`${u.workflow}${u.workflow_version}`}><Link to={`/workflows/${u.workflow}`}>{u.workflow}</Link> v{u.workflow_version} (utilise v{u.uses_versions.join(", v")})</li>)}</ul>}
        </Overlay>
      )}
      {panel === "deps" && (
        <Overlay title="Dépendances" description="Ce dont ce workflow a besoin pour s'exécuter." onClose={() => setPanel(null)} footer={<button type="button" onClick={() => setPanel(null)}>Fermer</button>}>
          <ul>
            {deps.data?.actions.map((a) => <li key={a.slug}>Action <Link to={`/library/actions/${a.slug}`}>{a.name ?? a.slug}</Link> {a.exists ? `(version ${a.pinned ?? a.latest ?? "?"}${a.deprecated ? ", dépréciée ⚠" : ""})` : "⚠ introuvable"}</li>)}
            {deps.data?.workflows.map((w) => <li key={w.slug}>Workflow <Link to={`/workflows/${w.slug}`}>{w.name ?? w.slug}</Link>{w.exists ? "" : " ⚠ introuvable"}</li>)}
            {deps.data?.connections.map((c) => <li key={c}>Connexion <Link to="/connections">{c}</Link></li>)}
            {deps.data?.ai_providers.map((p) => <li key={p}>Fournisseur d'IA {p}</li>)}
            {deps.data && !deps.data.actions.length && !deps.data.workflows.length && !deps.data.connections.length && !deps.data.ai_providers.length && <li>Aucune dépendance externe.</li>}
          </ul>
        </Overlay>
      )}
    </>
  );
  return { status, actions, notices, dialogs };
}

function Body() {
  const { tab = "summary" } = useParams();
  const d = useDraft();
  const parts = useHeaderParts();
  const current = TABS.find((t) => t.id === tab) ?? TABS[0];
  return (
    <EditorShell backTo="/workflows" backLabel="Retour aux workflows" title={d.wf.name} titleHint={d.wf.folder ? `Dossier : ${d.wf.folder}` : undefined}
      titleExtra={<FavoriteStar kind="workflow" slug={d.slug} name={d.wf.name} />} status={parts.status} actions={parts.actions} actionsLabel="Actions du workflow"
      fill={current?.id === "schema" ? "wf" : false}
      tabs={<PageTabs label="Sections du workflow" className="wf-tabs" active={current?.id ?? ""} tabs={TABS.map((t) => ({ id: t.id, label: t.label, to: `/workflows/${d.slug}/${t.id}` }))} />}
      notices={parts.notices}>
      <div className={`wf-content tab-${current?.id}`}>{current?.el}</div>
      {parts.dialogs}
    </EditorShell>
  );
}

export function WorkflowPage() {
  const { slug = "" } = useParams();
  return <DraftProvider slug={slug}><Body /></DraftProvider>;
}
