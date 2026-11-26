import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type ApiError } from "../../api";
import { SearchBox } from "../../components/AdminUi";
import { CopyBlock } from "../../components/CopyBlock";
import { Overlay } from "../../components/Overlay";
import { PageHeader } from "../../components/PageHeader";
import { useApp } from "../../context";
import { FormIcon, IconLabel } from "../../forms/FormIcon";
import { errorText, useWsQuery } from "../../lib";
import { Membership, NameField, VarsField } from "./InventoryFields";
import { GROUP_NAME, HOST_NAME, docProblem, removeGroup, renameGroup, toggleIn, type InvDoc, type InvGroup, type InvHost } from "./inventory";

export function InventoryPage() {
  const { slug = "" } = useParams();
  const { workspace, can } = useApp();
  const qc = useQueryClient();
  const detail = useWsQuery<InvDoc>("inventory", `/inventories/${slug}/`);
  const [doc, setDoc] = useState<InvDoc | null>(null);
  const [dirty, setDirty] = useState(false);
  const [q, setQ] = useState("");
  const [dialog, setDialog] = useState<"import" | "preview" | null>(null);
  const editable = can("edit");
  useEffect(() => {
    if (detail.data && !dirty) setDoc(detail.data);
  }, [detail.data, dirty]);
  const edit = (next: InvDoc) => {
    setDoc(next);
    setDirty(true);
  };
  const save = useMutation<InvDoc, ApiError, InvDoc>({
    mutationFn: (d) => api(`/workspaces/${workspace.slug}/inventories/${slug}/`, { method: "PUT", json: d }),
    onSuccess: (d) => {
      setDoc(d);
      setDirty(false);
      void qc.invalidateQueries({ queryKey: ["inventories"] });
      void qc.invalidateQueries({ queryKey: ["inventory"] });
    },
  });
  const problem = useMemo(() => (doc ? docProblem(doc) : null), [doc]);
  const groupNames = doc?.groups.map((g) => g.name) ?? [];
  const hosts = useMemo(() => (doc?.hosts ?? []).map((h, i) => ({ h, i })).filter(({ h }) => !q || h.name.toLowerCase().includes(q.toLowerCase())), [doc, q]);
  if (detail.isError)
    return (
      <>
        <PageHeader title="Inventaire introuvable" />
        <p>
          <Link to="/library/actions?tab=inventories">Retour aux inventaires</Link>
        </p>
      </>
    );
  if (!doc) return <PageHeader title="Inventaire" description="Chargement…" />;
  const setGroup = (i: number, patch: Partial<InvGroup>) => edit({ ...doc, groups: doc.groups.map((g, k) => (k === i ? { ...g, ...patch } : g)) });
  const setHost = (i: number, patch: Partial<InvHost>) => edit({ ...doc, hosts: doc.hosts.map((h, k) => (k === i ? { ...h, ...patch } : h)) });
  const newName = (base: string, taken: string[]) => {
    let n = 1;
    while (taken.includes(`${base}${n}`)) n++;
    return `${base}${n}`;
  };
  return (
    <>
      <PageHeader
        title={doc.name || "Inventaire"}
        description={
          <>
            <Link to="/library/actions?tab=inventories">← Inventaires</Link> · identifiant <code>{slug}</code> · une action Ansible le désigne avec{" "}
            <code>inventory_ref: {slug}</code>
          </>
        }
        actions={
          <>
            <button
              type="button"
              onClick={() => setDialog("preview")}
              disabled={dirty}
              title={dirty ? "Enregistrez d'abord : l'aperçu montre la version enregistrée" : undefined}
            >
              Aperçu Ansible
            </button>
            {editable && (
              <button type="button" onClick={() => setDialog("import")}>
                Importer YAML/JSON
              </button>
            )}
            {editable && (
              <button type="button" className="primary" disabled={!dirty || !!problem || save.isPending} onClick={() => save.mutate(doc)}>
                Enregistrer
              </button>
            )}
          </>
        }
      />
      {problem && (
        <p role="alert" className="field-error">
          {problem}
        </p>
      )}
      {save.error && (
        <p role="alert" className="field-error">
          {errorText(save.error)}
        </p>
      )}
      {dirty && !save.isPending && (
        <p role="status" className="hint">
          Modifications non enregistrées.
        </p>
      )}
      <section className="adm-card" aria-labelledby="inv-general">
        <h2 id="inv-general">Général</h2>
        <div className="field">
          <label htmlFor="inv-name">Nom</label>
          <input id="inv-name" value={doc.name} disabled={!editable} maxLength={160} onChange={(e) => edit({ ...doc, name: e.target.value })} />
        </div>
        <div className="field">
          <label htmlFor="inv-desc">Description</label>
          <input id="inv-desc" value={doc.description} disabled={!editable} maxLength={2000} onChange={(e) => edit({ ...doc, description: e.target.value })} />
        </div>
        <div className="field">
          <span className="label" id="inv-vars-l">
            Variables communes à toutes les machines
          </span>
          <VarsField
            id="inv-vars"
            label="Variables communes (JSON)"
            value={doc.variables}
            disabled={!editable}
            onChange={(v) => edit({ ...doc, variables: v })}
          />
        </div>
        <p className="hint">
          Aucun identifiant ici : une variable comme <code>ansible_password</code> ou <code>ansible_ssh_private_key_file</code> est refusée. Les clés et mots de
          passe passent par les connexions de l'action.
        </p>
      </section>
      <section className="adm-card" aria-labelledby="inv-groups">
        <h2 id="inv-groups">Groupes ({doc.groups.length})</h2>
        <div className="table-wrap">
          <table className="adm-table">
            <caption className="sr-only">Groupes de l'inventaire</caption>
            <thead>
              <tr>
                <th scope="col">Nom</th>
                <th scope="col">Sous-groupes</th>
                <th scope="col">Variables</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {doc.groups.map((g, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: une ligne n'a pas d'autre identité que sa place dans l'inventaire (son nom se modifie) ; ses champs suivent le document
                <tr key={i}>
                  <td>
                    <NameField
                      id={`g-name-${i}`}
                      label={`Nom du groupe ${i + 1}`}
                      value={g.name}
                      disabled={!editable}
                      invalid={!GROUP_NAME.test(g.name)}
                      onCommit={(to) => edit(renameGroup(doc, g.name, to))}
                    />
                  </td>
                  <td>
                    <Membership
                      label={`Sous-groupes de ${g.name}`}
                      all={groupNames.filter((n) => n !== g.name)}
                      chosen={g.children}
                      disabled={!editable}
                      onToggle={(n, on) => setGroup(i, { children: toggleIn(g.children, n, on) })}
                    />
                  </td>
                  <td>
                    <VarsField
                      id={`g-vars-${i}`}
                      label={`Variables du groupe ${g.name} (JSON)`}
                      value={g.variables}
                      disabled={!editable}
                      onChange={(v) => setGroup(i, { variables: v })}
                    />
                  </td>
                  <td className="row-actions">
                    {editable && (
                      <button
                        type="button"
                        className="icon-btn danger-icon"
                        onClick={() => edit(removeGroup(doc, g.name))}
                        aria-label={`Supprimer le groupe ${g.name}`}
                        title="Supprimer"
                      >
                        <FormIcon name="trash" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {doc.groups.length === 0 && (
                <tr>
                  <td colSpan={4} className="hint">
                    Aucun groupe. Les groupes permettent de cibler un ensemble de machines (« web », « db »…).
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {editable && (
          <button type="button" onClick={() => edit({ ...doc, groups: [...doc.groups, { name: newName("groupe", groupNames), variables: {}, children: [] }] })}>
            <IconLabel icon="plus">Ajouter un groupe</IconLabel>
          </button>
        )}
      </section>
      <section className="adm-card" aria-labelledby="inv-hosts">
        <h2 id="inv-hosts">Machines ({doc.hosts.length})</h2>
        <div className="adm-filters">
          <SearchBox label="Rechercher une machine" value={q} onChange={setQ} placeholder="Nom de la machine" />
          <span className="lst-count" role="status">
            {hosts.length} affichée(s)
          </span>
        </div>
        <div className="table-wrap">
          <table className="adm-table">
            <caption className="sr-only">Machines de l'inventaire</caption>
            <thead>
              <tr>
                <th scope="col">Nom</th>
                <th scope="col">Active</th>
                <th scope="col">Groupes</th>
                <th scope="col">Variables</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {hosts.map(({ h, i }) => (
                <tr key={i}>
                  <td>
                    <NameField
                      id={`h-name-${i}`}
                      label={`Nom de la machine ${i + 1}`}
                      value={h.name}
                      disabled={!editable}
                      invalid={!HOST_NAME.test(h.name)}
                      onCommit={(to) => setHost(i, { name: to })}
                    />
                  </td>
                  <td>
                    <label className="inline-check">
                      <input type="checkbox" checked={h.enabled} disabled={!editable} onChange={(e) => setHost(i, { enabled: e.target.checked })} />
                      <span className="sr-only">Machine {h.name} active</span>
                    </label>
                  </td>
                  <td>
                    <Membership
                      label={`Groupes de ${h.name}`}
                      all={groupNames}
                      chosen={h.groups}
                      disabled={!editable}
                      onToggle={(n, on) => setHost(i, { groups: toggleIn(h.groups, n, on) })}
                    />
                  </td>
                  <td>
                    <VarsField
                      id={`h-vars-${i}`}
                      label={`Variables de ${h.name} (JSON)`}
                      value={h.variables}
                      disabled={!editable}
                      onChange={(v) => setHost(i, { variables: v })}
                    />
                  </td>
                  <td className="row-actions">
                    {editable && (
                      <button
                        type="button"
                        className="icon-btn danger-icon"
                        onClick={() => edit({ ...doc, hosts: doc.hosts.filter((_, k) => k !== i) })}
                        aria-label={`Supprimer la machine ${h.name}`}
                        title="Supprimer"
                      >
                        <FormIcon name="trash" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {doc.hosts.length === 0 && (
                <tr>
                  <td colSpan={5} className="hint">
                    Aucune machine.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {editable && (
          <button
            type="button"
            onClick={() =>
              edit({
                ...doc,
                hosts: [
                  ...doc.hosts,
                  {
                    name: newName(
                      "machine",
                      doc.hosts.map((h) => h.name),
                    ),
                    variables: {},
                    enabled: true,
                    groups: [],
                  },
                ],
              })
            }
          >
            <IconLabel icon="plus">Ajouter une machine</IconLabel>
          </button>
        )}
        <p className="hint">
          Une machine désactivée reste dans l'inventaire mais n'est jamais passée à Ansible. Pour une adresse différente du nom : variable{" "}
          <code>ansible_host</code>.
        </p>
      </section>
      {dialog === "preview" && <Preview slug={slug} onClose={() => setDialog(null)} />}
      {dialog === "import" && (
        <ImportDialog
          onClose={() => setDialog(null)}
          onImported={(d) => {
            edit({ ...doc, variables: d.variables, groups: d.groups, hosts: d.hosts });
            setDialog(null);
          }}
        />
      )}
    </>
  );
}

interface Rendered {
  all?: { hosts?: Record<string, unknown>; vars?: Record<string, unknown>; children?: Record<string, { hosts?: Record<string, unknown> }> };
}

/** Ce que recevra Ansible, en chiffres : machines jouées (sans doublon), groupes, variables communes. */
export function renderedCounts(r: Rendered | undefined): { hosts: number; groups: number; vars: number } {
  const all = r?.all ?? {};
  const names = new Set(Object.keys(all.hosts ?? {}));
  for (const g of Object.values(all.children ?? {})) for (const h of Object.keys(g.hosts ?? {})) names.add(h);
  return { hosts: names.size, groups: Object.keys(all.children ?? {}).length, vars: Object.keys(all.vars ?? {}).length };
}

function Preview({ slug, onClose }: { slug: string; onClose: () => void }) {
  const r = useWsQuery<Rendered>("inventory-rendered", `/inventories/${slug}/rendered/`);
  const n = renderedCounts(r.data);
  return (
    <Overlay
      variant="modal"
      wide
      title="Aperçu Ansible"
      description="L'inventaire enregistré, tel que le recevra le playbook. Les machines désactivées en sont absentes."
      onClose={onClose}
      footer={
        <button type="button" onClick={onClose}>
          Fermer
        </button>
      }
    >
      {r.isError ? (
        <p role="alert" className="field-error">
          Aperçu indisponible. Réessayez.
        </p>
      ) : !r.data ? (
        <p className="hint">Chargement…</p>
      ) : (
        <div className="inv-preview">
          <ul className="inv-stats" aria-label="Contenu de l'inventaire">
            <li>
              <strong>{n.hosts}</strong> machine{n.hosts > 1 ? "s" : ""} active{n.hosts > 1 ? "s" : ""}
            </li>
            <li>
              <strong>{n.groups}</strong> groupe{n.groups > 1 ? "s" : ""}
            </li>
            <li>
              <strong>{n.vars}</strong> variable{n.vars > 1 ? "s" : ""} commune{n.vars > 1 ? "s" : ""}
            </li>
          </ul>
          {n.hosts === 0 && (
            <p role="note" className="inv-warn">
              Aucune machine active : une action Ansible qui désigne cet inventaire échouera (« inventaire vide »). Ajoutez une machine, ou réactivez-en une,
              puis enregistrez.
            </p>
          )}
          <h3 className="inv-preview-title">Inventaire au format JSON</h3>
          <CopyBlock label="Inventaire au format JSON" text={JSON.stringify(r.data, null, 2)} />
        </div>
      )}
    </Overlay>
  );
}

function ImportDialog({ onClose, onImported }: { onClose: () => void; onImported: (d: Pick<InvDoc, "variables" | "groups" | "hosts">) => void }) {
  const { workspace } = useApp();
  const [text, setText] = useState("");
  const run = useMutation<Pick<InvDoc, "variables" | "groups" | "hosts">, ApiError, void>({
    mutationFn: () => api(`/workspaces/${workspace.slug}/inventories/import/`, { json: { text } }),
    onSuccess: onImported,
  });
  return (
    <Overlay
      variant="modal"
      title="Importer un inventaire"
      description="Collez un inventaire Ansible au format YAML ou JSON. Il REMPLACE les machines, groupes et variables en cours d'édition ; rien n'est enregistré avant « Enregistrer »."
      onClose={onClose}
      footer={
        <>
          <button type="button" onClick={onClose}>
            Annuler
          </button>
          <button type="button" className="primary" disabled={!text.trim() || run.isPending} onClick={() => run.mutate()}>
            Importer
          </button>
        </>
      }
    >
      <div className="field">
        <label htmlFor="inv-import">Inventaire YAML ou JSON</label>
        <textarea
          id="inv-import"
          rows={12}
          spellCheck={false}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={"all:\n  children:\n    web:\n      hosts:\n        web1: {ansible_host: 10.0.0.5}"}
        />
      </div>
      {run.error && (
        <p role="alert" className="field-error">
          {errorText(run.error)}
        </p>
      )}
    </Overlay>
  );
}
