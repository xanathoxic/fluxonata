import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type ApiError } from "../../api";
import { Confirm, EmptyRow, SearchBox } from "../../components/AdminUi";
import { FolderTree, inFolder, useFolders } from "../../components/FolderTree";
import { MoveFolderOverlay } from "../../components/MoveFolderOverlay";
import { Overlay } from "../../components/Overlay";
import { RowIcon } from "../../components/RowIcon";
import { PageHeader } from "../../components/PageHeader";
import { useApp } from "../../context";
import { IconLabel, FormIcon } from "../../forms/FormIcon";
import { slugify } from "../../forms/library";
import { formatDate } from "../../i18n";
import { errorText, useWsQuery } from "../../lib";
import type { InvDoc, InvSummary } from "./inventory";

/** « Inventaires » : les machines et les groupes que les playbooks Ansible ciblent (ADR 0037). Un playbook s'y réfère par son identifiant (`inventory_ref`). */
export function InventoriesPage({ embedded = false }: { embedded?: boolean }) {
  const { workspace, can } = useApp();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const list = useWsQuery<InvSummary[]>("inventories", "/inventories/");
  const [creating, setCreating] = useState(false);
  const [folder, setFolder] = useState("");
  const [search, setSearch] = useState("");
  const [moving, setMoving] = useState<{ ref: string; label: string; folder: string } | null>(null);
  const [toDelete, setToDelete] = useState<InvSummary | null>(null);
  const all = list.data ?? [];
  const move = useMutation<unknown, ApiError, { ref: string; folder: string }>({
    mutationFn: (i) => api(`/workspaces/${workspace.slug}/inventories/${i.ref}/`, { method: "PATCH", json: { folder: i.folder } }),
    onSuccess: () => {
      setMoving(null);
      void qc.invalidateQueries({ queryKey: ["inventories"] });
      void qc.invalidateQueries({ queryKey: ["folders"] });
    },
  });
  const folders = useMemo(() => all.map((x) => x.folder), [all]);
  const created = useFolders("inventory").data?.results ?? [];
  const shown = all.filter(
    (x) =>
      inFolder(x.folder, folder, !!search.trim()) &&
      (!search.trim() || `${x.name} ${x.slug} ${x.description}`.toLowerCase().includes(search.trim().toLowerCase())),
  );
  const del = useMutation<unknown, ApiError, string>({
    mutationFn: (slug) => api(`/workspaces/${workspace.slug}/inventories/${slug}/`, { method: "DELETE" }),
    onSuccess: () => {
      setToDelete(null);
      void qc.invalidateQueries({ queryKey: ["inventories"] });
    },
  });
  return (
    <>
      <PageHeader
        sub={embedded}
        title="Inventaires"
        description={`${all.length} inventaire(s). Un inventaire liste les machines et leurs groupes ; une action Ansible le désigne par son identifiant (entrée « inventory_ref »). Aucun identifiant n'y est conservé : les clés et mots de passe passent par les connexions.`}
        actions={
          can("edit") ? (
            <button type="button" className="primary" aria-haspopup="dialog" onClick={() => setCreating(true)}>
              <IconLabel icon="plus">Nouvel inventaire</IconLabel>
            </button>
          ) : undefined
        }
      />
      <div className="wf-list-layout">
        <FolderTree label="Dossiers d'inventaires" kind="inventory" canEdit={can("edit")} folders={folders} current={folder} onPick={setFolder} />
        <section className="adm-card lst-main">
          <div className="adm-filters">
            <SearchBox label="Rechercher un inventaire" value={search} onChange={setSearch} placeholder="Nom ou description" />
            <span className="lst-count" role="status">
              {shown.length} inventaire(s)
            </span>
          </div>
          {list.isError && <p role="alert">Une erreur est survenue. Réessayez.</p>}
          <div className="table-wrap">
            <table className="adm-table">
              <caption className="sr-only">Inventaires Ansible</caption>
              <thead>
                <tr>
                  <th scope="col">Nom</th>
                  <th scope="col">Dossier</th>
                  <th scope="col">Identifiant</th>
                  <th scope="col">Machines</th>
                  <th scope="col">Groupes</th>
                  <th scope="col">Modifié</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {shown.map((i) => (
                  <tr key={i.slug}>
                    <td>
                      <Link className="lst-title" to={`/library/inventories/${i.slug}`}>
                        {i.name}
                      </Link>
                      {i.description && <span className="lst-sub">{i.description}</span>}
                    </td>
                    <td>{i.folder ? <span className="tag">{i.folder}</span> : "—"}</td>
                    <td>
                      <code>{i.slug}</code>
                    </td>
                    <td>{i.hosts}</td>
                    <td>{i.groups}</td>
                    <td>{formatDate(i.updated_at)}</td>
                    <td className="row-actions">
                      <Link
                        className="icon-btn"
                        to={`/library/inventories/${i.slug}`}
                        aria-label={`${can("edit") ? "Modifier" : "Consulter"} ${i.name}`}
                        title={can("edit") ? "Modifier" : "Consulter"}
                      >
                        <FormIcon name={can("edit") ? "edit" : "eye"} />
                      </Link>
                      {can("edit") && (
                        <button
                          type="button"
                          className="icon-btn"
                          onClick={() => {
                            move.reset();
                            setMoving({ ref: i.slug, label: i.name, folder: i.folder });
                          }}
                          aria-label={`Déplacer l'inventaire ${i.name} dans un dossier`}
                          title="Déplacer"
                        >
                          <RowIcon name="folder" />
                        </button>
                      )}
                      {can("edit") && (
                        <button
                          type="button"
                          className="icon-btn danger-icon"
                          onClick={() => {
                            del.reset();
                            setToDelete(i);
                          }}
                          aria-label={`Supprimer ${i.name}`}
                          title="Supprimer"
                        >
                          <FormIcon name="trash" />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {all.length === 0 && (
                  <EmptyRow cols={7}>
                    {list.isLoading ? (
                      "Chargement…"
                    ) : all.length > 0 ? (
                      "Rien dans ce dossier."
                    ) : (
                      <>Aucun inventaire. {can("edit") ? "Créez-en un pour cibler des machines avec Ansible." : ""}</>
                    )}
                  </EmptyRow>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>
      {creating && (
        <NewInventory
          existing={all}
          folder={folder}
          onClose={() => setCreating(false)}
          onCreated={(slug) => {
            void qc.invalidateQueries({ queryKey: ["inventories"] });
            navigate(`/library/inventories/${slug}`);
          }}
        />
      )}
      {moving && (
        <MoveFolderOverlay
          title={`Déplacer « ${moving.label} »`}
          current={moving.folder}
          folders={[...folders, ...created]}
          busy={move.isPending}
          error={move.error?.message}
          onSave={(f) => move.mutate({ ref: moving.ref, folder: f })}
          onClose={() => setMoving(null)}
        />
      )}
      {toDelete && (
        <Confirm
          title={`Supprimer « ${toDelete.name} » ?`}
          confirmLabel="Supprimer"
          busy={del.isPending}
          onConfirm={() => del.mutate(toDelete.slug)}
          onCancel={() => setToDelete(null)}
        >
          {del.error ? (
            <span role="alert" className="field-error">
              {errorText(del.error)}
            </span>
          ) : (
            "Les actions Ansible qui le désignent échoueront (« inventaire introuvable ») tant qu'il n'est pas recréé sous le même identifiant."
          )}
        </Confirm>
      )}
    </>
  );
}

function NewInventory({
  existing,
  folder,
  onClose,
  onCreated,
}: {
  existing: InvSummary[];
  folder: string;
  onClose: () => void;
  onCreated: (slug: string) => void;
}) {
  const { workspace } = useApp();
  const [name, setName] = useState("");
  const slug = slugify(name);
  const taken = existing.some((i) => i.slug === slug);
  const create = useMutation<InvDoc, ApiError, void>({
    mutationFn: () => api(`/workspaces/${workspace.slug}/inventories/`, { json: { slug, name, description: "", folder } }),
    onSuccess: (d) => onCreated(d.slug),
  });
  return (
    <Overlay
      variant="modal"
      title="Nouvel inventaire"
      description="Vous ajouterez ensuite les machines et les groupes."
      onClose={onClose}
      footer={
        <>
          <button type="button" onClick={onClose}>
            Annuler
          </button>
          <button type="submit" form="ni-form" className="primary" disabled={!name.trim() || !slug || taken || create.isPending}>
            Créer
          </button>
        </>
      }
    >
      <form
        id="ni-form"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <div className="field">
          <label htmlFor="ni-name">Nom de l'inventaire</label>
          {/* biome-ignore lint/a11y/noAutofocus: le champ reçoit le focus à l'ouverture de la boîte de dialogue */}
          <input id="ni-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={160} required autoFocus />
          {slug && (
            <p className="hint">
              Identifiant : <code>{slug}</code>
              {taken && " — déjà utilisé"}
            </p>
          )}
        </div>
        {create.error && (
          <p role="alert" className="field-error">
            {errorText(create.error)}
          </p>
        )}
      </form>
    </Overlay>
  );
}
