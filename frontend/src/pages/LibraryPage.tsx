import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, apiAll, type Action, type ApiError } from "../api";
import { FolderTree, inFolder, useFolders } from "../components/FolderTree";
import { MoveFolderOverlay } from "../components/MoveFolderOverlay";
import { Overlay } from "../components/Overlay";
import { PageHeader } from "../components/PageHeader";
import { RowIcon } from "../components/RowIcon";
import { Tabs } from "../components/Tabs";
import { useApp } from "../context";
import { NewActionForm } from "./library/NewActionForm";
import { PackagesTab } from "./library/PackagesTab";

function ActionsTab() {
  const { can, workspace } = useApp();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["actions-lib", workspace.slug], queryFn: () => apiAll<Action>(`/workspaces/${workspace.slug}/actions/`) });
  const [creating, setCreating] = useState(false);
  const [folder, setFolder] = useState("");
  const [search, setSearch] = useState("");
  const [moving, setMoving] = useState<Action | null>(null);
  const move = useMutation<unknown, ApiError, { slug: string; folder: string }>({
    mutationFn: (i) => api(`/workspaces/${workspace.slug}/actions/${i.slug}/meta/`, { method: "PATCH", json: { folder: i.folder } }),
    onSuccess: () => { setMoving(null); void qc.invalidateQueries({ queryKey: ["actions-lib"] }); void qc.invalidateQueries({ queryKey: ["actions"] }); },
  });
  const all = q.data?.results ?? [];
  const folders = useMemo(() => all.map((a) => a.folder), [all]);
  const created = useFolders("action").data?.results ?? [];
  const shown = all.filter((a) => inFolder(a.folder, folder) && (!search || `${a.name} ${a.description} ${a.domain}`.toLowerCase().includes(search.toLowerCase())));
  return (
    <div className="wf-list-layout">
      <FolderTree label="Dossiers d'actions" kind="action" canEdit={can("publish")} folders={folders} current={folder} onPick={setFolder} />
      <div>
        <div className="toolbar-row">
          <div className="field"><label htmlFor="act-search">Rechercher une action</label><input id="act-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)} /></div>
          <p className="hint" style={{ flex: 1 }}>Les actions sont les briques exécutables : chaque version publiée est immuable.</p>
          {can("publish") && <button type="button" className="primary" onClick={() => setCreating(true)}>Nouvelle action</button>}
        </div>
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Actions de la bibliothèque</caption>
            <thead><tr><th scope="col">Action</th><th scope="col">Dossier</th><th scope="col">Domaine</th><th scope="col">Version</th><th scope="col">Statut</th>{can("publish") && <th scope="col"><span className="sr-only">Actions</span></th>}</tr></thead>
            <tbody>
              {shown.map((a) => (
                <tr key={a.slug}><td><Link to={`/library/actions/${a.slug}`}>{a.name}</Link></td><td>{a.folder || "—"}</td><td>{a.domain || "—"}</td><td>{a.latest_version?.version ?? "—"}</td>
                  <td>{a.status === "deprecated" ? `⚠ Dépréciée${a.deprecation_reason ? ` : ${a.deprecation_reason}` : ""}` : "✔ Active"}</td>
                  {can("publish") && <td><button type="button" className="icon-btn" onClick={() => { move.reset(); setMoving(a); }} aria-label={`Déplacer l'action ${a.name} dans un dossier`} title="Déplacer"><RowIcon name="folder" /></button></td>}</tr>
              ))}
              {q.data && shown.length === 0 && <tr><td colSpan={6} className="hint">Aucune action ici.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
      {creating && <Overlay wide title="Nouvelle action" description="Code, contrat d'entrées et de sorties : la version 1.0.0 est publiée et immuable." onClose={() => setCreating(false)}><NewActionForm folder={folder} onDone={() => setCreating(false)} /></Overlay>}
      {moving && <MoveFolderOverlay title={`Déplacer « ${moving.name} »`} current={moving.folder} folders={[...folders, ...created]} busy={move.isPending} error={move.error?.message} onSave={(f) => move.mutate({ slug: moving.slug, folder: f })} onClose={() => setMoving(null)} />}
    </div>
  );
}

/** Library > Actions: executable building blocks, organised in folders like workflows, plus importable packages. */
export function LibraryPage() {
  const [tab, setTab] = useState("actions");
  return (
    <>
      <PageHeader title="Actions" description="Briques exécutables réutilisables dans les workflows, rangées par dossiers, et paquets importables." />
      <Tabs label="Bibliothèque d'actions" active={tab} onChange={setTab} tabs={[
        { id: "actions", label: "Actions", content: <ActionsTab /> },
        { id: "packages", label: "Paquets", content: <PackagesTab /> },
      ]} />
    </>
  );
}
