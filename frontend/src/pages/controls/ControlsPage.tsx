import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type ApiError } from "../../api";
import { Badge, Confirm, EmptyRow, SearchBox, SortTh, usePaged, useSort } from "../../components/AdminUi";
import { FolderTree, inFolder, useFolders } from "../../components/FolderTree";
import { MoveFolderOverlay } from "../../components/MoveFolderOverlay";
import { Overlay } from "../../components/Overlay";
import { RowIcon } from "../../components/RowIcon";
import { PageHeader } from "../../components/PageHeader";
import { TagFilter, TagList } from "../../components/Tags";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { errorText } from "../../lib";
import { controlIcon, emptySpec, MODES, type ControlCard, type ControlMode } from "../../forms/controls";
import { FormIcon, IconLabel, type FormIconName } from "../../forms/FormIcon";
import { slugify } from "../../forms/library";

/** « Contrôles » : the library of custom controls (a group of fields, repeated lines, boxes with details…), published in versions
 * and then offered in the palette of the form editor. */
export function ControlsPage() {
  const { workspace, can } = useApp();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [tagF, setTagF] = useState("");
  const [creating, setCreating] = useState(false);
  const [folder, setFolder] = useState("");
  const [moving, setMoving] = useState<ControlCard | null>(null);
  const [toDelete, setToDelete] = useState<ControlCard | null>(null);
  const list = useQuery({ queryKey: ["control-library", workspace.slug], refetchOnMount: "always", queryFn: () => api<{ results: ControlCard[] }>(`/workspaces/${workspace.slug}/control-library/`) });
  const all = list.data?.results ?? [];
  const shown = useMemo(() => all.filter((c) => inFolder(c.folder, folder) && (!tagF || c.tags.includes(tagF)) && (!q || `${c.name} ${c.description} ${c.tags.join(" ")}`.toLowerCase().includes(q.toLowerCase()))), [all, folder, q, tagF]);
  const { sorted, sort, toggle } = useSort(shown, { name: (c) => c.name, folder: (c) => c.folder, mode: (c) => c.mode ?? "", used: (c) => c.used_by ?? 0, updated: (c) => Date.parse(c.updated_at) || 0 }, { key: "updated", dir: "desc" });
  const paged = usePaged(sorted, 25);
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["control-library"] }); void qc.invalidateQueries({ queryKey: ["control-library-published"] }); };
  const created = useFolders("control").data?.results ?? [];
  const move = useMutation<unknown, ApiError, { slug: string; folder: string }>({
    mutationFn: ({ slug, folder: f }) => api(`/workspaces/${workspace.slug}/control-library/${slug}/`, { method: "PATCH", json: { folder: f } }),
    onSuccess: () => { setMoving(null); refresh(); void qc.invalidateQueries({ queryKey: ["folders"] }); },
  });
  const del = useMutation<unknown, ApiError, string>({
    mutationFn: (slug) => api(`/workspaces/${workspace.slug}/control-library/${slug}/`, { method: "DELETE" }),
    onSuccess: () => { setToDelete(null); refresh(); },
  });
  return (
    <>
      <PageHeader title="Contrôles" description={`${all.length} contrôle(s) personnalisé(s). Un contrôle réunit plusieurs champs en un seul (groupe, lignes répétées, liste à cocher avec détails) ; une fois publié, il apparaît dans la palette de l'éditeur de formulaires.`} />
      <div className="wf-list-layout">
      <FolderTree label="Dossiers de contrôles" kind="control" canEdit={can("edit")} folders={all.map((c) => c.folder)} current={folder} onPick={setFolder} />
      <section className="adm-card lst-main">
        <div className="adm-filters">
          <SearchBox label="Rechercher un contrôle" value={q} onChange={setQ} placeholder="Nom, description ou étiquette" />
          <TagFilter lists={all.map((c) => c.tags)} value={tagF} onChange={setTagF} />
          <span className="lst-count" role="status">{shown.length} résultat(s)</span>
          <span style={{ flex: 1 }} />
          {can("edit") && <button type="button" className="primary" aria-haspopup="dialog" onClick={() => setCreating(true)}><IconLabel icon="plus">Nouveau contrôle</IconLabel></button>}
        </div>
        {list.isError && <p role="alert">Une erreur est survenue. Réessayez.</p>}
        <div className="table-wrap">
          <table className="adm-table">
            <caption className="sr-only">Contrôles personnalisés</caption>
            <thead><tr><SortTh label="Nom" field="name" sort={sort} onSort={toggle} /><SortTh label="Dossier" field="folder" sort={sort} onSort={toggle} /><SortTh label="Type" field="mode" sort={sort} onSort={toggle} /><th scope="col">Étiquettes</th><th scope="col">Version</th><th scope="col">Sous-champs</th><SortTh label="Utilisation" field="used" sort={sort} onSort={toggle} /><SortTh label="Modifié" field="updated" sort={sort} onSort={toggle} /><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {paged.slice.map((c) => {
                const mode = MODES.find((m) => m.id === c.mode);
                return (
                  <tr key={c.slug}>
                    <td><Link className="lst-title" to={`/library/controls/${c.slug}`}><FormIcon name={controlIcon(c.icon)} size={16} /> {c.name}</Link>{c.description && <span className="lst-sub">{c.description}</span>}</td>
                    <td>{c.folder ? <span className="tag">{c.folder}</span> : "—"}</td>
                    <td>{mode ? <Badge tone="info"><IconLabel icon={mode.icon}>{mode.label}</IconLabel></Badge> : "—"}</td>
                    <td>{c.tags.length > 0 ? <TagList tags={c.tags} onPick={setTagF} active={tagF} /> : "—"}</td>
                    <td>{c.latest_version ? <Badge tone="info">v{c.latest_version.version}</Badge> : <Badge tone="draft">brouillon</Badge>}</td>
                    <td>{c.parts}</td>
                    <td>{c.used_by ? `${c.used_by} formulaire${c.used_by > 1 ? "s" : ""}` : "—"}</td>
                    <td>{formatDate(c.updated_at)}</td>
                    <td className="row-actions">
                      <Link className="icon-btn" to={`/library/controls/${c.slug}`} aria-label={`${can("edit") ? "Modifier" : "Consulter"} ${c.name}`} title={can("edit") ? "Modifier" : "Consulter"}><FormIcon name={can("edit") ? "edit" : "eye"} /></Link>
                      {can("edit") && <button type="button" className="icon-btn" onClick={() => { move.reset(); setMoving(c); }} aria-label={`Déplacer ${c.name} dans un dossier`} title="Déplacer"><RowIcon name="folder" /></button>}
                      {can("edit") && <button type="button" className="icon-btn danger-icon" onClick={() => setToDelete(c)} aria-label={`Supprimer ${c.name}`} title="Supprimer"><FormIcon name="trash" /></button>}
                    </td>
                  </tr>
                );
              })}
              {shown.length === 0 && <EmptyRow cols={9}>{list.isLoading ? "Chargement…" : <>Aucun contrôle. {can("edit") ? "Créez-en un pour l'utiliser dans vos formulaires." : ""}</>}</EmptyRow>}
            </tbody>
          </table>
        </div>
        {paged.pager("Pagination des contrôles")}
      </section>
      </div>
      {moving && <MoveFolderOverlay title={`Déplacer « ${moving.name} »`} current={moving.folder} folders={[...all.map((c) => c.folder), ...created]} busy={move.isPending} error={move.error?.message} onSave={(f) => move.mutate({ slug: moving.slug, folder: f })} onClose={() => setMoving(null)} />}
      {creating && <NewControl folder={folder} existing={all} onClose={() => setCreating(false)} onCreated={(slug) => { refresh(); navigate(`/library/controls/${slug}`); }} />}
      {toDelete && (
        <Confirm title={`Supprimer « ${toDelete.name} » ?`} confirmLabel="Supprimer" busy={del.isPending} onConfirm={() => del.mutate(toDelete.slug)} onCancel={() => setToDelete(null)}>
          {del.error ? <span role="alert" className="field-error">{errorText(del.error)}</span> : "Les formulaires qui l'utilisent déjà gardent leur propre copie de la version employée ; il ne sera plus proposé dans la palette."}
        </Confirm>
      )}
    </>
  );
}

function NewControl({ folder, existing, onClose, onCreated }: { folder: string; existing: ControlCard[]; onClose: () => void; onCreated: (slug: string) => void }) {
  const { workspace } = useApp();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"group" | "repeat" | "list">("group");
  const [multiple, setMultiple] = useState(false);
  const mode: ControlMode = kind === "list" ? (multiple ? "items" : "choice") : kind;
  const slug = slugify(name);
  const taken = existing.some((c) => c.slug === slug);
  const create = useMutation<ControlCard, ApiError, void>({
    mutationFn: () => api(`/workspaces/${workspace.slug}/control-library/`, { json: { slug, name, folder, spec: emptySpec(mode), icon: MODES.find((m) => m.id === mode)?.icon ?? "custom" } }),
    onSuccess: (c) => onCreated(c.slug),
  });
  return (
    <Overlay variant="modal" title="Nouveau contrôle" description="Donnez un nom et choisissez le type : il ne pourra plus être changé ensuite." onClose={onClose}
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="submit" form="nc-form" className="primary" disabled={!name.trim() || !slug || taken || create.isPending}>Créer</button></>}>
      <form id="nc-form" onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }}>
        <div className="field"><label htmlFor="nc-name">Nom du contrôle</label><input id="nc-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={160} required autoFocus />
          {slug && <p className="hint">Identifiant : <code>{slug}</code>{taken && " — déjà utilisé"}</p>}</div>
        <div className="ctl-modes" role="radiogroup" aria-label="Type de contrôle">
          {KINDS.map((k) => (
            <button key={k.id} type="button" role="radio" aria-checked={kind === k.id} className={`ctl-mode${kind === k.id ? " on" : ""}`} onClick={() => setKind(k.id)}>
              <FormIcon name={k.icon} size={22} /><strong>{k.label}</strong><span>{k.hint}</span>
            </button>
          ))}
        </div>
        {kind === "list" && (
          <div className="field field-inline"><input id="nc-multi" type="checkbox" checked={multiple} onChange={(e) => setMultiple(e.target.checked)} /><label htmlFor="nc-multi">Plusieurs choix possibles</label></div>
        )}
        {create.error && <p role="alert" className="field-error">Erreur : {errorText(create.error)}</p>}
      </form>
    </Overlay>
  );
}

/** What is asked when a control is created (the choice and the list of boxes are one kind, with or without several choices). */
const KINDS: { id: "group" | "repeat" | "list"; label: string; hint: string; icon: FormIconName }[] = [
  { id: "group", label: "Groupe", hint: "Plusieurs champs réunis en un seul contrôle (une adresse, une personne…). La valeur est un objet.", icon: "layers" },
  { id: "repeat", label: "Lignes répétées", hint: "Le même groupe de champs, répété autant de fois que nécessaire, en fiches ou en tableau. La valeur est une liste.", icon: "repeat" },
  { id: "list", label: "Liste de choix", hint: "Des options (en menu, boutons ou cases), qui reprennent toutes le même modèle de champs de détail. Un ou plusieurs choix.", icon: "boxes" },
];
