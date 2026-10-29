import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, apiAll, type Action, type ApiError } from "../api";
import { Badge, EmptyRow, SearchBox, SortTh, usePaged, useSort } from "../components/AdminUi";
import { FolderTree, inFolder, useFolders } from "../components/FolderTree";
import { MoveFolderOverlay } from "../components/MoveFolderOverlay";
import { Overlay } from "../components/Overlay";
import { PageHeader } from "../components/PageHeader";
import { RowIcon } from "../components/RowIcon";
import { Tabs } from "../components/Tabs";
import { useApp } from "../context";
import { NewActionForm } from "./library/NewActionForm";
import { PackagesTab } from "./library/PackagesTab";
import { HelpTip } from "../components/HelpTip";
import { TagFilter, TagList } from "../components/Tags";

function ActionsTab() {
  const { can, workspace } = useApp();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["actions-lib", workspace.slug], queryFn: () => apiAll<Action>(`/workspaces/${workspace.slug}/actions/`) });
  const [creating, setCreating] = useState(false);
  const [folder, setFolder] = useState("");
  const [search, setSearch] = useState("");
  const [tagF, setTagF] = useState("");
  const [moving, setMoving] = useState<Action | null>(null);
  const move = useMutation<unknown, ApiError, { slug: string; folder: string }>({
    mutationFn: (i) => api(`/workspaces/${workspace.slug}/actions/${i.slug}/meta/`, { method: "PATCH", json: { folder: i.folder } }),
    onSuccess: () => { setMoving(null); void qc.invalidateQueries({ queryKey: ["actions-lib"] }); void qc.invalidateQueries({ queryKey: ["actions"] }); },
  });
  const all = q.data?.results ?? [];
  const folders = useMemo(() => all.map((a) => a.folder), [all]);
  const created = useFolders("action").data?.results ?? [];
  const shown = all.filter((a) => inFolder(a.folder, folder) && (!tagF || (a.tags ?? []).includes(tagF)) && (!search || `${a.name} ${a.description} ${a.domain} ${(a.tags ?? []).join(" ")}`.toLowerCase().includes(search.toLowerCase())));
  const { sorted, sort, toggle } = useSort(shown, { name: (a) => a.name, folder: (a) => a.folder, domain: (a) => a.domain, version: (a) => a.latest_version?.version ?? "", status: (a) => a.status }, { key: "name", dir: "asc" });
  const paged = usePaged(sorted, 25);
  return (
    <div className="wf-list-layout">
      <FolderTree label="Dossiers d'actions" kind="action" canEdit={can("publish")} folders={folders} current={folder} onPick={setFolder} />
      <section className="adm-card lst-main">
        <div className="adm-filters">
          <SearchBox label="Rechercher une action" value={search} onChange={setSearch} placeholder="Nom, description, domaine ou étiquette" />
            <TagFilter lists={all.map((a) => a.tags)} value={tagF} onChange={setTagF} />
          <span className="lst-count" role="status">{shown.length} action(s)</span>
          <span style={{ flex: 1 }} />
          {can("publish") && <button type="button" className="primary" onClick={() => setCreating(true)}>Nouvelle action</button>}
        </div>
        <p className="help-row"><HelpTip caption="À savoir">Les actions sont les briques exécutables : chaque version publiée est immuable.</HelpTip></p>
        <div className="table-wrap">
          <table className="adm-table">
            <caption className="sr-only">Actions de la bibliothèque</caption>
            <thead><tr><SortTh label="Action" field="name" sort={sort} onSort={toggle} /><SortTh label="Dossier" field="folder" sort={sort} onSort={toggle} /><th scope="col">Étiquettes</th><SortTh label="Domaine" field="domain" sort={sort} onSort={toggle} /><SortTh label="Version" field="version" sort={sort} onSort={toggle} /><th scope="col">Contrat</th><SortTh label="Statut" field="status" sort={sort} onSort={toggle} />{can("publish") && <th scope="col"><span className="sr-only">Actions</span></th>}</tr></thead>
            <tbody>
              {paged.slice.map((a) => (
                <tr key={a.slug}><td><Link className="lst-title" to={`/library/actions/${a.slug}`}>{a.name}</Link></td><td>{a.folder ? <span className="tag">{a.folder}</span> : "—"}</td><td>{(a.tags?.length ?? 0) > 0 ? <TagList tags={a.tags} onPick={setTagF} active={tagF} /> : "—"}</td><td>{a.domain || "—"}</td><td>{a.latest_version ? <Badge tone="info">v{a.latest_version.version}</Badge> : "—"}</td>
                  <td>{a.contract_complete === false ? <Badge tone="warn">⚠ Incomplet</Badge> : a.contract_complete ? <Badge tone="ok">✔ Complet</Badge> : "—"}</td>
                  <td>{a.status === "deprecated" ? <Badge tone="warn">{`⚠ Dépréciée${a.deprecation_reason ? ` : ${a.deprecation_reason}` : ""}`}</Badge> : <Badge tone="ok">✔ Active</Badge>}</td>
                  {can("publish") && <td><button type="button" className="icon-btn" onClick={() => { move.reset(); setMoving(a); }} aria-label={`Déplacer l'action ${a.name} dans un dossier`} title="Déplacer"><RowIcon name="folder" /></button></td>}</tr>
              ))}
              {shown.length === 0 && <EmptyRow cols={8}>{q.isLoading ? "Chargement…" : "Aucune action ici."}</EmptyRow>}
            </tbody>
          </table>
        </div>
        {paged.pager("Pagination des actions")}
      </section>
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
