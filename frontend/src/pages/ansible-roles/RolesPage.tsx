import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type ApiError } from "../../api";
import { Badge, Confirm, EmptyRow, SearchBox } from "../../components/AdminUi";
import { FolderTree, inFolder, useFolders } from "../../components/FolderTree";
import { MoveFolderOverlay } from "../../components/MoveFolderOverlay";
import { Overlay } from "../../components/Overlay";
import { RowIcon } from "../../components/RowIcon";
import { PageHeader } from "../../components/PageHeader";
import { useApp } from "../../context";
import { FormIcon, IconLabel } from "../../forms/FormIcon";
import { errorText, useWsQuery } from "../../lib";
import { ROLE_NAME, STARTER_ROLE, type RoleSummary } from "./roles";

/** « Rôles Ansible » : les rôles que les playbooks appellent par leur nom (ADR 0037). Chaque version est immuable ; une action déclare les rôles qu'elle utilise. */
export function RolesPage({ embedded = false }: { embedded?: boolean }) {
  const { workspace, can } = useApp();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const list = useWsQuery<RoleSummary[]>("ansible-roles", "/ansible-roles/");
  const [creating, setCreating] = useState(false);
  const [folder, setFolder] = useState("");
  const [search, setSearch] = useState("");
  const [moving, setMoving] = useState<{ ref: string; label: string; folder: string } | null>(null);
  const [toDelete, setToDelete] = useState<RoleSummary | null>(null);
  const all = list.data ?? [];
  const move = useMutation<unknown, ApiError, { ref: string; folder: string }>({
    mutationFn: (i) => api(`/workspaces/${workspace.slug}/ansible-roles/${i.ref}/`, { method: "PATCH", json: { folder: i.folder } }),
    onSuccess: () => {
      setMoving(null);
      void qc.invalidateQueries({ queryKey: ["ansible-roles"] });
      void qc.invalidateQueries({ queryKey: ["folders"] });
    },
  });
  const folders = useMemo(() => all.map((x) => x.folder), [all]);
  const created = useFolders("ansible_role").data?.results ?? [];
  const shown = all.filter(
    (x) => inFolder(x.folder, folder, !!search.trim()) && (!search.trim() || `${x.name} ${x.description}`.toLowerCase().includes(search.trim().toLowerCase())),
  );
  const del = useMutation<unknown, ApiError, string>({
    mutationFn: (name) => api(`/workspaces/${workspace.slug}/ansible-roles/${name}/`, { method: "DELETE" }),
    onSuccess: () => {
      setToDelete(null);
      void qc.invalidateQueries({ queryKey: ["ansible-roles"] });
    },
  });
  return (
    <>
      <PageHeader
        sub={embedded}
        title="Rôles Ansible"
        description={`${all.length} rôle(s). Un rôle réunit des tâches, des modèles et des valeurs par défaut réutilisables ; un playbook l'appelle par son nom (« roles: [web] » ou « include_role »). Chaque version est immuable : une action en déclare une, qui est figée à sa publication.`}
        actions={
          can("publish") ? (
            <button type="button" className="primary" aria-haspopup="dialog" onClick={() => setCreating(true)}>
              <IconLabel icon="plus">Nouveau rôle</IconLabel>
            </button>
          ) : undefined
        }
      />
      <div className="wf-list-layout">
        <FolderTree label="Dossiers de rôles" kind="ansible_role" canEdit={can("publish")} folders={folders} current={folder} onPick={setFolder} />
        <section className="adm-card lst-main">
          <div className="adm-filters">
            <SearchBox label="Rechercher un rôle" value={search} onChange={setSearch} placeholder="Nom ou description" />
            <span className="lst-count" role="status">
              {shown.length} rôle(s)
            </span>
          </div>
          {list.isError && <p role="alert">Une erreur est survenue. Réessayez.</p>}
          <div className="table-wrap">
            <table className="adm-table">
              <caption className="sr-only">Rôles Ansible</caption>
              <thead>
                <tr>
                  <th scope="col">Nom</th>
                  <th scope="col">Dossier</th>
                  <th scope="col">Contrat</th>
                  <th scope="col">Dernière version</th>
                  <th scope="col">Versions</th>
                  <th scope="col">Utilisé par</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.name}>
                    <td>
                      <Link className="lst-title" to={`/library/ansible-roles/${r.name}`}>
                        {r.name}
                      </Link>
                      {(r.summary || r.description) && <span className="lst-sub">{r.summary || r.description}</span>}
                    </td>
                    <td>{r.folder ? <span className="tag">{r.folder}</span> : "—"}</td>
                    <td>
                      {r.contract_ok ? <Badge tone="ok">Complet</Badge> : <Badge tone="warn">À compléter</Badge>}
                      <span className="hint"> {r.variables} variable(s)</span>
                    </td>
                    <td>{r.latest_version ?? "—"}</td>
                    <td>{r.versions.length}</td>
                    <td>{r.used_by ? `${r.used_by} version${r.used_by > 1 ? "s" : ""} d'action` : "—"}</td>
                    <td className="row-actions">
                      <Link
                        className="icon-btn"
                        to={`/library/ansible-roles/${r.name}`}
                        aria-label={`${can("publish") ? "Modifier" : "Consulter"} ${r.name}`}
                        title={can("publish") ? "Modifier" : "Consulter"}
                      >
                        <FormIcon name={can("publish") ? "edit" : "eye"} />
                      </Link>
                      {can("publish") && (
                        <button
                          type="button"
                          className="icon-btn"
                          onClick={() => {
                            move.reset();
                            setMoving({ ref: r.name, label: r.name, folder: r.folder });
                          }}
                          aria-label={`Déplacer le rôle ${r.name} dans un dossier`}
                          title="Déplacer"
                        >
                          <RowIcon name="folder" />
                        </button>
                      )}
                      {can("publish") && (
                        <button
                          type="button"
                          className="icon-btn danger-icon"
                          onClick={() => {
                            del.reset();
                            setToDelete(r);
                          }}
                          aria-label={`Supprimer ${r.name}`}
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
                      <>Aucun rôle. {can("publish") ? "Créez-en un pour le réutiliser dans vos playbooks." : ""}</>
                    )}
                  </EmptyRow>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>
      {creating && (
        <NewRole
          existing={all}
          folder={folder}
          onClose={() => setCreating(false)}
          onCreated={(name) => {
            void qc.invalidateQueries({ queryKey: ["ansible-roles"] });
            navigate(`/library/ansible-roles/${name}`);
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
          title={`Supprimer le rôle « ${toDelete.name} » ?`}
          confirmLabel="Supprimer"
          busy={del.isPending}
          onConfirm={() => del.mutate(toDelete.name)}
          onCancel={() => setToDelete(null)}
        >
          {del.error ? (
            <span role="alert" className="field-error">
              {errorText(del.error)}
            </span>
          ) : (
            "Toutes ses versions sont supprimées. Un rôle que des actions utilisent ne peut pas l'être."
          )}
        </Confirm>
      )}
    </>
  );
}

function NewRole({
  existing,
  folder,
  onClose,
  onCreated,
}: {
  existing: RoleSummary[];
  folder: string;
  onClose: () => void;
  onCreated: (name: string) => void;
}) {
  const { workspace } = useApp();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const taken = existing.some((r) => r.name === name);
  const valid = ROLE_NAME.test(name);
  const create = useMutation<RoleSummary, ApiError, void>({
    mutationFn: () =>
      api(`/workspaces/${workspace.slug}/ansible-roles/`, { json: { name, description, folder, files: STARTER_ROLE, changelog: "Première version" } }),
    onSuccess: (r) => onCreated(r.name),
  });
  return (
    <Overlay
      variant="modal"
      title="Nouveau rôle"
      description="Le rôle commence par un fichier de tâches et un fichier de valeurs par défaut ; vous les modifierez ensuite."
      onClose={onClose}
      footer={
        <>
          <button type="button" onClick={onClose}>
            Annuler
          </button>
          <button type="submit" form="nr-form" className="primary" disabled={!valid || taken || create.isPending}>
            Créer
          </button>
        </>
      }
    >
      <form
        id="nr-form"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <div className="field">
          <label htmlFor="nr-name">Nom du rôle</label>
          <input
            id="nr-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={50}
            required
            // biome-ignore lint/a11y/noAutofocus: le champ reçoit le focus à l'ouverture de la boîte de dialogue
            autoFocus
            spellCheck={false}
            aria-describedby="nr-hint"
          />
          <p id="nr-hint" className="hint">
            Lettres minuscules, chiffres et soulignés, en commençant par une lettre (ex. <code>web</code>, <code>base_systeme</code>). C'est le nom que le
            playbook appelle.
          </p>
          {name && !valid && (
            <p role="alert" className="field-error">
              Ce nom n'est pas valide.
            </p>
          )}
          {taken && (
            <p role="alert" className="field-error">
              Ce nom existe déjà.
            </p>
          )}
        </div>
        <div className="field">
          <label htmlFor="nr-desc">Description (facultatif)</label>
          <input id="nr-desc" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={2000} />
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
