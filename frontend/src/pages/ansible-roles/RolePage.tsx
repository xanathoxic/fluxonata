import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type ApiError } from "../../api";
import { Confirm } from "../../components/AdminUi";
import { Overlay } from "../../components/Overlay";
import { Tabs } from "../../components/Tabs";
import { RoleContract } from "./RoleContract";
import { RoleIde } from "./RoleIde";
import { PageHeader } from "../../components/PageHeader";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { errorText, useWsQuery } from "../../lib";
import {
  contractAdvice,
  DIRS,
  filesProblem,
  firstFile,
  isSemver,
  nextVersion,
  pathProblem,
  type RoleContractDoc,
  type RoleSummary,
  type RoleVersionFiles,
  sameContract,
  sameFiles,
  starter,
} from "./roles";

/** Un rôle : ses versions (immuables), ses fichiers, et la publication d'une nouvelle version à partir des fichiers modifiés. */
export function RolePage() {
  const { name = "" } = useParams();
  const { workspace, can } = useApp();
  const qc = useQueryClient();
  const role = useWsQuery<RoleSummary>("ansible-role", `/ansible-roles/${name}/`);
  const latest = role.data?.latest_version ?? null;
  const [shown, setShown] = useState<string | null>(null); // la version regardée ; par défaut la dernière
  const version = shown ?? latest;
  const detail = useWsQuery<RoleVersionFiles>(`ansible-role-v-${version ?? "none"}`, `/ansible-roles/${name}/versions/${version}/`, { enabled: !!version });
  const [files, setFiles] = useState<Record<string, string>>({});
  const [contract, setContract] = useState<RoleContractDoc>({});
  const [tab, setTab] = useState("files");
  const [selected, setSelected] = useState<string | null>(null);
  const [tabs, setTabs] = useState<string[]>([]); // les fichiers ouverts, comme les onglets d'un éditeur de code
  const [dialog, setDialog] = useState<"file" | "folder" | "refresh" | "publish" | null>(null);
  const editable = can("publish");
  useEffect(() => {
    if (!detail.data) return;
    setFiles(detail.data.files);
    setContract(detail.data.contract ?? {});
    const first = firstFile(detail.data.files) ?? null;
    setSelected((cur) => (cur && cur in detail.data.files ? cur : first));
    setTabs((cur) => {
      const kept = cur.filter((p) => p in detail.data.files);
      return kept.length > 0 ? kept : first ? [first] : [];
    });
  }, [detail.data]);
  const dirty = !!detail.data && (!sameFiles(files, detail.data.files) || !sameContract(contract, detail.data.contract));
  const problem = useMemo(() => filesProblem(files), [files]);
  const publish = useMutation<RoleSummary, ApiError, { version: string; changelog: string }>({
    mutationFn: (b) => api(`/workspaces/${workspace.slug}/ansible-roles/${name}/versions/`, { json: { ...b, files, contract } }),
    onSuccess: (r) => {
      setShown(r.latest_version);
      setDialog(null);
      void qc.invalidateQueries({ queryKey: ["ansible-role"] });
      void qc.invalidateQueries({ queryKey: ["ansible-roles"] });
    },
  });
  if (role.isError)
    return (
      <>
        <PageHeader title="Rôle introuvable" />
        <p>
          <Link to="/library/actions?tab=roles">Retour aux rôles</Link>
        </p>
      </>
    );
  if (!role.data) return <PageHeader title="Rôle Ansible" description="Chargement…" />;
  const old = !!version && version !== latest;
  const canEdit = editable && !old;
  const open = (p: string) => {
    setTabs((cur) => (cur.includes(p) ? cur : [...cur, p]));
    setSelected(p);
  };
  const close = (p: string) => {
    const rest = tabs.filter((t) => t !== p);
    setTabs(rest);
    if (selected === p) setSelected(rest[rest.length - 1] ?? null);
  };
  const remove = (p: string) => {
    const { [p]: _gone, ...rest } = files;
    void _gone;
    setFiles(rest);
    setTabs((cur) => cur.filter((t) => t !== p));
    if (selected === p) setSelected(tabs.filter((t) => t !== p).pop() ?? firstFile(rest) ?? null);
  };
  return (
    <>
      <PageHeader
        title={role.data.name}
        description={
          <>
            <Link to="/library/actions?tab=roles">← Rôles Ansible</Link>
            {role.data.description && <> · {role.data.description}</>} · utilisé par {role.data.used_by} version(s) d'action · dans un playbook :{" "}
            <code>roles: [{role.data.name}]</code>
          </>
        }
        actions={
          editable ? (
            <button
              type="button"
              className="primary"
              disabled={!dirty || !!problem || publish.isPending || old}
              onClick={() => {
                publish.reset();
                setDialog("publish");
              }}
            >
              Publier une nouvelle version…
            </button>
          ) : undefined
        }
      />
      <div className="field-inline">
        <label htmlFor="role-v">Version</label>
        <select
          id="role-v"
          value={version ?? ""}
          onChange={(e) => {
            setShown(e.target.value);
          }}
        >
          {role.data.versions.map((v) => (
            <option key={v.version} value={v.version}>
              {v.version}
              {v.version === latest ? " (dernière)" : ""} · {formatDate(v.created_at)}
            </option>
          ))}
        </select>
        {detail.data?.changelog && <span className="hint"> — {detail.data.changelog}</span>}
        {detail.data && (
          <span className="hint">
            {" "}
            · empreinte <code>{detail.data.digest.slice(0, 19)}…</code>
          </span>
        )}
      </div>
      {old && (
        <p role="note" className="hint">
          Version ancienne : lecture seule. Une version publiée ne change jamais ; pour repartir d'elle, copiez ses fichiers dans une nouvelle version depuis la
          dernière.
        </p>
      )}
      {problem && dirty && (
        <p role="alert" className="field-error">
          {problem}
        </p>
      )}
      {dirty && (
        <p role="status" className="hint">
          Modifications non publiées : elles ne sont enregistrées qu'avec « Publier une nouvelle version ».
        </p>
      )}
      <Tabs
        label="Sections du rôle"
        active={tab}
        onChange={setTab}
        tabs={[
          {
            id: "files",
            label: "Fichiers",
            content: (
              <RoleIde
                roleName={role.data.name}
                version={version}
                files={files}
                original={detail.data?.files ?? {}}
                tabs={tabs}
                selected={selected}
                canEdit={canEdit}
                old={old}
                onOpen={open}
                onClose={close}
                onChange={(p, text) => setFiles((cur) => ({ ...cur, [p]: text }))}
                onDelete={remove}
                onAdd={() => setDialog("file")}
                onAddFolder={() => setDialog("folder")}
                onRefresh={() => (dirty ? setDialog("refresh") : void detail.refetch())}
              />
            ),
          },
          {
            id: "contract",
            label: contractAdvice(contract).length > 0 ? "Contrat (à compléter)" : "Contrat",
            content: <RoleContract role={role.data.name} contract={contract} files={files} canEdit={canEdit} onChange={setContract} />,
          },
        ]}
      />
      {dialog === "refresh" && (
        <Confirm
          title="Abandonner les modifications non publiées ?"
          confirmLabel="Abandonner"
          onConfirm={() => {
            setFiles(detail.data?.files ?? {});
            setContract(detail.data?.contract ?? {});
            setDialog(null);
            void detail.refetch();
          }}
          onCancel={() => setDialog(null)}
        >
          Les fichiers reviennent à la version {version} telle qu'elle est publiée.
        </Confirm>
      )}
      {(dialog === "file" || dialog === "folder") && (
        <AddFile
          folder={dialog === "folder"}
          existing={Object.keys(files)}
          onClose={() => setDialog(null)}
          onAdd={(p) => {
            setFiles({ ...files, [p]: starter(p) });
            open(p);
            setDialog(null);
          }}
        />
      )}
      {dialog === "publish" && (
        <Publish latest={latest} busy={publish.isPending} error={publish.error} onClose={() => setDialog(null)} onPublish={(b) => publish.mutate(b)} />
      )}
    </>
  );
}

function AddFile({ existing, folder, onClose, onAdd }: { existing: string[]; folder: boolean; onClose: () => void; onAdd: (path: string) => void }) {
  const missing = DIRS.filter((d) => !existing.some((p) => p.startsWith(`${d.name}/`)));
  const [path, setPath] = useState(folder ? `${missing[0]?.name ?? "tasks"}/main.yml` : "tasks/");
  const why = pathProblem(path) ?? (existing.includes(path) ? "Ce fichier existe déjà." : null);
  return (
    <Overlay
      variant="modal"
      title={folder ? "Ajouter un dossier" : "Ajouter un fichier"}
      description={
        folder
          ? "Un dossier de rôle n'existe que par ses fichiers : indiquez le premier, par exemple vars/main.yml."
          : "Le chemin est relatif au rôle : tasks/installer.yml, templates/page.j2, defaults/main.yml…"
      }
      onClose={onClose}
      footer={
        <>
          <button type="button" onClick={onClose}>
            Annuler
          </button>
          <button type="submit" form="af-form" className="primary" disabled={!!why}>
            Ajouter
          </button>
        </>
      }
    >
      <form
        id="af-form"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          if (!why) onAdd(path);
        }}
      >
        <div className="field">
          <label htmlFor="af-path">Chemin du fichier</label>
          <input
            id="af-path"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            spellCheck={false}
            // biome-ignore lint/a11y/noAutofocus: le champ reçoit le focus à l'ouverture de la boîte de dialogue
            autoFocus
            aria-describedby="af-hint"
          />
          <p id="af-hint" className="hint">
            {folder && missing.length > 0
              ? `Dossiers pas encore créés : ${missing.map((d) => d.name).join(", ")}.`
              : `Dossiers possibles : ${DIRS.map((d) => d.name).join(", ")}.`}
          </p>
          {path.length > 0 && why && (
            <p role="alert" className="field-error">
              {why}
            </p>
          )}
        </div>
      </form>
    </Overlay>
  );
}

function Publish({
  latest,
  busy,
  error,
  onClose,
  onPublish,
}: {
  latest: string | null;
  busy: boolean;
  error: ApiError | null;
  onClose: () => void;
  onPublish: (b: { version: string; changelog: string }) => void;
}) {
  const [version, setVersion] = useState(nextVersion(latest));
  const [changelog, setChangelog] = useState("");
  const ok = isSemver(version);
  return (
    <Overlay
      variant="modal"
      title="Publier une nouvelle version"
      description="Une version publiée ne change plus jamais. Les actions déjà publiées gardent la version qu'elles avaient figée ; les nouvelles peuvent choisir celle-ci."
      onClose={onClose}
      footer={
        <>
          <button type="button" onClick={onClose}>
            Annuler
          </button>
          <button type="submit" form="pv-form" className="primary" disabled={!ok || busy}>
            Publier
          </button>
        </>
      }
    >
      <form
        id="pv-form"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          if (ok) onPublish({ version, changelog });
        }}
      >
        <div className="field">
          <label htmlFor="pv-v">Numéro de version</label>
          <input id="pv-v" value={version} onChange={(e) => setVersion(e.target.value)} spellCheck={false} aria-describedby="pv-hint" />
          <p id="pv-hint" className="hint">
            Supérieur à la dernière version ({latest ?? "aucune"}), par exemple {nextVersion(latest)}.
          </p>
          {!ok && (
            <p role="alert" className="field-error">
              Un numéro du type 1.2.0 est attendu.
            </p>
          )}
        </div>
        <div className="field">
          <label htmlFor="pv-c">Ce qui change (facultatif)</label>
          <input id="pv-c" value={changelog} onChange={(e) => setChangelog(e.target.value)} maxLength={300} />
        </div>
        {error && (
          <p role="alert" className="field-error">
            {errorText(error)}
          </p>
        )}
      </form>
    </Overlay>
  );
}
