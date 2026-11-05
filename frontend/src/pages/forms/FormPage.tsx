import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, apiAll, type ApiError, type WorkflowSummary } from "../../api";
import { Badge, Confirm, EmptyRow } from "../../components/AdminUi";
import { EditorShell } from "../../components/EditorShell";
import { Menu } from "../../components/Menu";
import { Overlay } from "../../components/Overlay";
import { PageTabs } from "../../components/PageTabs";
import { StatusLine } from "../../components/StatusLine";
import { useAutosave } from "../../components/useAutosave";
import { PageHeader } from "../../components/PageHeader";
import { TagInput } from "../../components/Tags";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { errorText, useWsQuery } from "../../lib";
import { DefinitionEditor } from "../../forms/DefinitionEditor";
import { DefinitionForm } from "../../forms/DefinitionForm";
import { FormIcon, IconLabel } from "../../forms/FormIcon";
import { formDiff } from "../../forms/formDiff";
import { slugify, type FormCard, type FormFull } from "../../forms/library";
import { HTTP_KINDS, type FormDefinition } from "../../forms/types";
import "../../forms/forms.css";

const TABS = ["design", "summary", "versions", "usage"] as const;
type TabId = (typeof TABS)[number];

/** One form of the library: its designer, its details, its published versions and where it is used. */
export function FormPage() {
  const { slug = "" } = useParams();
  return <FormEditor key={slug} slug={slug} />; // one instance per form: what is pending is saved for THIS form when leaving
}

function FormEditor({ slug }: { slug: string }) {
  const { tab = "design" } = useParams();
  const { workspace, can } = useApp();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const edit = can("edit");
  const key = ["form-lib", workspace.slug, slug];
  const detail = useQuery({ queryKey: key, queryFn: () => api<FormFull>(`/workspaces/${workspace.slug}/form-library/${slug}/`) });
  const [def, setDef] = useState<FormDefinition | null>(null);
  const [baseline, setBaseline] = useState<{ value: FormDefinition; revision: number } | null>(null);
  const [dialog, setDialog] = useState<null | "publish" | "copy" | "template" | "delete">(null);
  const [modelWf, setModelWf] = useState("");
  const wfs = useQuery({ queryKey: ["workflows", workspace.slug], queryFn: () => apiAll<WorkflowSummary>(`/workspaces/${workspace.slug}/workflows/`) });
  const conns = useWsQuery<{ results: { name: string; kind: string }[] }>("connections", "/connections/");

  useEffect(() => { if (detail.data && def === null) { setDef(detail.data.definition); setBaseline({ value: detail.data.definition, revision: detail.data.draft_revision }); } }, [detail.data, def]);

  const refresh = () => { void qc.invalidateQueries({ queryKey: key }); void qc.invalidateQueries({ queryKey: ["form-library", workspace.slug] }); };
  const auto = useAutosave<FormDefinition>({
    storageKey: `fluxonata.draft.form.${workspace.slug}.${slug}`, value: def, baseline, enabled: edit, paused: dialog === "publish",
    save: async (v, revision) => {
      const r = await api<FormCard>(`/workspaces/${workspace.slug}/form-library/${slug}/`, { method: "PATCH", json: { definition: v, revision } });
      void qc.invalidateQueries({ queryKey: ["form-library", workspace.slug] });
      return { revision: r.draft_revision };
    },
    onRecover: setDef,
  });
  const reload = async () => {
    const r = await detail.refetch();
    if (r.data) { auto.discardLocal(); setDef(r.data.definition); setBaseline({ value: r.data.definition, revision: r.data.draft_revision }); }
  };
  const change = (d: FormDefinition) => setDef(d);
  const schema = wfs.data?.results.find((w) => w.slug === modelWf)?.latest_version?.input_schema;
  const connections = (conns.data?.results ?? []).filter((c) => HTTP_KINDS.includes(c.kind)).map((c) => c.name);
  const d = detail.data;

  if (detail.isError) return <><PageHeader title="Formulaire introuvable" /><p><Link to="/library/forms">Retour aux formulaires</Link></p></>;
  if (!d || def === null) return <p role="status">Chargement…</p>;
  const active: TabId = (TABS as readonly string[]).includes(tab) ? (tab as TabId) : "design";
  const refs = (
    <label className="fb-refsel" title="Entrées de référence : propose les entrées d'un workflow comme clés des champs">
      <FormIcon name="link" size={16} /><span className="sr-only">Entrées de référence</span>
      <select id="fl-model" value={modelWf} onChange={(e) => setModelWf(e.target.value)} aria-label="Entrées de référence">
        <option value="">Entrées de référence : aucune</option>
        {(wfs.data?.results ?? []).filter((w) => w.latest_version).map((w) => <option key={w.slug} value={w.slug}>{w.name}</option>)}
      </select>
    </label>
  );
  const usage = d.usage.workflows.length + d.usage.public_forms.length;
  const published = d.is_template ? "Modèle" : d.latest_version ? `v${d.latest_version.version} publiée` : "Jamais publié";
  return (
    <EditorShell backTo="/library/forms" backLabel="Retour aux formulaires" title={d.name} titleHint={d.description || undefined}
      status={<StatusLine published={published} state={auto.state} savedAt={auto.savedAt} extra={`${usage} rattachement(s)`} />}
      actions={edit && (
        <>
          {!d.is_template && can("publish") && <button type="button" className="primary" onClick={() => setDialog("publish")} aria-haspopup="dialog"><IconLabel icon="send">Publier</IconLabel></button>}
          <Menu label="Plus" ariaLabel="Plus d'actions" align="end" entries={[
            { label: "Dupliquer", onSelect: () => setDialog("copy") },
            ...(!d.is_template ? [{ label: "Enregistrer comme modèle", onSelect: () => setDialog("template") }] : []),
            { kind: "separator" as const },
            { label: "Supprimer", danger: true, onSelect: () => setDialog("delete") },
          ]} />
        </>
      )}
      tabs={<PageTabs label="Formulaire" active={active} onChange={(t) => navigate(`/library/forms/${slug}/${t}`)} tabs={[
        { id: "design", label: "Conception", icon: "design" }, { id: "summary", label: "Informations", icon: "list" },
        { id: "versions", label: "Versions", icon: "layers", badge: d.versions.length || undefined }, { id: "usage", label: "Utilisation", icon: "link", badge: usage || undefined },
      ]} />}
      notices={<>
        {auto.state === "conflict" && <p role="alert" className="conflict">Ce formulaire a été modifié ailleurs. <button type="button" onClick={() => void reload()}>Recharger la version du serveur</button> <button type="button" onClick={() => void auto.overwrite()}>Écraser avec ma version</button></p>}
        {auto.state === "invalid" && <p role="alert" className="field-error">Le serveur refuse ce brouillon : {auto.message || "contenu invalide"}. Vos modifications restent dans ce navigateur ; corrigez-les, l'enregistrement reprendra tout seul.</p>}
        {auto.recover && <p role="alert" className="conflict">Des modifications non enregistrées ont été retrouvées dans ce navigateur. <button type="button" onClick={auto.acceptRecovery}>Les récupérer</button> <button type="button" onClick={auto.discardRecovery}>Les ignorer</button></p>}
      </>}>
      <div id="fp-panel" role="tabpanel" aria-label={active} className="tabpanel">
        {active === "design" && <DefinitionEditor def={def} onChange={change} edit={edit} schema={schema} connections={connections} toolbarExtra={refs} />}
        {active === "summary" && <Summary form={d} edit={edit} onSaved={refresh} />}
        {active === "versions" && <Versions form={d} edit={edit} />}
        {active === "usage" && <Usage form={d} />}
      </div>
      {dialog === "publish" && <Publish form={d} dirty={auto.dirty} def={def} saveFirst={async () => { if (!(await auto.flush())) throw new Error("Le brouillon n'a pas pu être enregistré : corrigez-le ou résolvez le conflit avant de publier."); }} onClose={() => setDialog(null)} onDone={() => { setDialog(null); refresh(); }} />}
      {(dialog === "copy" || dialog === "template") && <Copy form={d} asTemplate={dialog === "template"} onClose={() => setDialog(null)} onDone={(s) => { setDialog(null); refresh(); navigate(`/library/forms/${s}`); }} />}
      {dialog === "delete" && <Delete form={d} onClose={() => setDialog(null)} onDone={() => { refresh(); navigate("/library/forms"); }} />}
    </EditorShell>
  );
}

function Summary({ form, edit, onSaved }: { form: FormFull; edit: boolean; onSaved: () => void }) {
  const { workspace } = useApp();
  const [name, setName] = useState(form.name);
  const [description, setDescription] = useState(form.description);
  const [folder, setFolder] = useState(form.folder);
  const [tags, setTags] = useState(form.tags);
  const patch = useMutation<unknown, ApiError, void>({ mutationFn: () => api(`/workspaces/${workspace.slug}/form-library/${form.slug}/`, { method: "PATCH", json: { name, description, folder, tags } }), onSuccess: onSaved });
  return (
    <section className="fb-panel" aria-label="Résumé du formulaire">
      <form onSubmit={(e: FormEvent) => { e.preventDefault(); patch.mutate(); }}>
        <fieldset disabled={!edit} style={{ border: 0, padding: 0, margin: 0 }}>
          <div className="field"><label htmlFor="fs-name">Nom</label><input id="fs-name" value={name} onChange={(e) => setName(e.target.value)} required /></div>
          <div className="field"><label htmlFor="fs-desc">Description</label><textarea id="fs-desc" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} /></div>
          <div className="field"><label htmlFor="fs-folder">Dossier</label><input id="fs-folder" value={folder} onChange={(e) => setFolder(e.target.value)} /></div>
          <TagInput value={tags} onChange={setTags} suggestions={[]} />
          <p className="hint">Identifiant : <code>{form.slug}</code> · créé le {formatDate(form.created_at)}{form.owner ? ` par ${form.owner}` : ""} · {form.fields} champ(s)</p>
          {edit && <p><button type="submit" className="primary" disabled={patch.isPending}><IconLabel icon="save">Enregistrer</IconLabel></button></p>}
          {patch.error && <p role="alert" className="field-error">Erreur : {errorText(patch.error)}</p>}
          {patch.isSuccess && <p role="status" className="hint">Informations enregistrées.</p>}
        </fieldset>
      </form>
    </section>
  );
}

function Versions({ form, edit }: { form: FormFull; edit: boolean }) {
  const { workspace } = useApp();
  const navigate = useNavigate();
  const [open, setOpen] = useState<string | null>(null);
  const one = useQuery({ queryKey: ["form-ver", workspace.slug, form.slug, open], enabled: open !== null, queryFn: () => api<{ definition: FormDefinition }>(`/workspaces/${workspace.slug}/form-library/${form.slug}/versions/${open}/`) });
  const copy = useMutation<FormCard, ApiError, string>({
    mutationFn: (v) => api(`/workspaces/${workspace.slug}/form-library/${form.slug}/duplicate/`, { json: { slug: slugify(`${form.name} ${v} ${Date.now().toString().slice(-4)}`), name: `${form.name} (${v})`, from_version: v } }),
    onSuccess: (f) => navigate(`/library/forms/${f.slug}`),
  });
  const [values, setValues] = useState<Record<string, unknown>>({});
  return (
    <section className="adm-card" aria-label="Versions publiées">
      <div className="table-wrap"><table className="adm-table">
        <caption className="sr-only">Versions publiées</caption>
        <thead><tr><th scope="col">Version</th><th scope="col">Publiée le</th><th scope="col">Notes</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>
          {form.versions.map((v) => (
            <tr key={v.version}>
              <td><Badge tone="info">v{v.version}</Badge></td><td>{formatDate(v.created_at)}</td><td>{v.changelog || "—"}</td>
              <td className="row-actions">
                <button type="button" className="icon-btn neutral-icon" onClick={() => { setValues({}); setOpen(v.version); }} aria-label={`Voir la version ${v.version}`} title="Voir"><FormIcon name="eye" /></button>
                {edit && <button type="button" className="icon-btn neutral-icon" onClick={() => copy.mutate(v.version)} aria-label={`Créer un formulaire à partir de la version ${v.version}`} title="Créer un formulaire à partir de cette version"><FormIcon name="copy" /></button>}
              </td>
            </tr>
          ))}
          {form.versions.length === 0 && <EmptyRow cols={4}>Aucune version publiée : publiez le formulaire pour pouvoir le rattacher.</EmptyRow>}
        </tbody>
      </table></div>
      {copy.error && <p role="alert" className="field-error">Erreur : {errorText(copy.error)}</p>}
      {open && (
        <Overlay title={`Version ${open}`} description="Aperçu en lecture seule : une version publiée ne change jamais." onClose={() => setOpen(null)} footer={<button type="button" onClick={() => setOpen(null)}>Fermer</button>}>
          {one.data ? <DefinitionForm definition={one.data.definition} values={values} onChange={setValues} /> : <p role="status">Chargement…</p>}
        </Overlay>
      )}
    </section>
  );
}

function Usage({ form }: { form: FormFull }) {
  const u = form.usage;
  const none = u.workflows.length === 0 && u.public_forms.length === 0;
  return (
    <section className="fb-panel" aria-label="Utilisation du formulaire">
      {none && <p className="hint">Ce formulaire n'est rattaché à rien. Depuis un workflow (onglet « Formulaire d'entrée ») ou un déclencheur public, choisissez-le pour l'utiliser.</p>}
      {u.workflows.length > 0 && (
        <>
          <h2>Workflows</h2>
          <ul>{u.workflows.map((w) => <li key={w.slug}><Link to={`/workflows/${w.slug}/form`}>{w.slug}</Link> — {w.versions.length ? `versions publiées : ${w.versions.join(", ")}` : "pas encore publié"}{w.in_draft ? " · utilisé dans le brouillon" : ""}</li>)}</ul>
        </>
      )}
      {u.public_forms.length > 0 && (
        <>
          <h2>Formulaires publics</h2>
          <ul>{u.public_forms.map((p) => <li key={p.id}>{p.name} (workflow {p.workflow}) — version {p.version ?? "?"}</li>)}</ul>
        </>
      )}
    </section>
  );
}

const KINDS = [
  { id: "patch", label: "Correction", hint: "un libellé, une aide ou une règle ajustés : rien ne change pour le workflow" },
  { id: "minor", label: "Ajout compatible", hint: "de nouveaux champs : les lancements existants continuent de fonctionner" },
  { id: "major", label: "Changement incompatible", hint: "un champ retiré ou de type différent : à annoncer aux workflows rattachés" },
];

/** The publication window: what changes since the last version, who uses this form, and the kind of change (suggested from the changes). */
function Publish({ form, dirty, def, saveFirst, onClose, onDone }: { form: FormFull; dirty: boolean; def: FormDefinition; saveFirst: () => Promise<unknown>; onClose: () => void; onDone: () => void }) {
  const { workspace } = useApp();
  const last = form.latest_version?.version ?? null;
  const prev = useQuery({ queryKey: ["form-ver", workspace.slug, form.slug, last], enabled: last !== null, queryFn: () => api<{ definition: FormDefinition }>(`/workspaces/${workspace.slug}/form-library/${form.slug}/versions/${last}/`) });
  const diff = useMemo(() => (last !== null && !prev.data ? null : formDiff(prev.data?.definition ?? null, def)), [last, prev.data, def]);
  const [changelog, setChangelog] = useState("");
  const [chosen, setChosen] = useState<string | null>(null);
  const bump = chosen ?? diff?.suggested ?? "minor";
  const go = useMutation<unknown, ApiError, void>({
    mutationFn: async () => { if (dirty) await saveFirst(); return api(`/workspaces/${workspace.slug}/form-library/${form.slug}/publish/`, { json: { changelog, bump } }); },
    onSuccess: onDone,
  });
  const fields = useMemo(() => def.sections.reduce((n, s) => n + s.fields.length, 0), [def]);
  const users = form.usage.workflows;
  const list = (title: string, items: string[]) => items.length > 0 && <li><strong>{title} :</strong> {items.join(", ")}</li>;
  return (
    <Overlay variant="modal" title="Publier une version" description={`Le brouillon (${fields} champ(s)) devient une version immuable${last ? `, la suivante après v${last}` : " (1.0.0)"}.${dirty ? " Il sera d'abord enregistré." : ""}`} onClose={onClose}
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="submit" form="fp-form" className="primary" disabled={go.isPending}>Publier</button></>}>
      <form id="fp-form" onSubmit={(e: FormEvent) => { e.preventDefault(); go.mutate(); }}>
        <section aria-label="Changements depuis la dernière version">
          <h3>Ce qui change</h3>
          {diff === null ? <p role="status" className="hint">Comparaison en cours…</p> : last === null ? <p className="hint">Première version : tous les champs sont nouveaux.</p>
            : diff.added.length + diff.removed.length + diff.changed.length + diff.typeChanged.length === 0 ? <p className="hint">Aucun champ n'a changé depuis v{last} (la mise en page ou le style peuvent avoir changé).</p>
            : <ul>{list("Ajoutés", diff.added)}{list("Retirés", diff.removed)}{list("Type modifié", diff.typeChanged)}{list("Modifiés", diff.changed)}</ul>}
        </section>
        <section aria-label="Utilisation">
          <h3>Qui l'utilise</h3>
          {users.length === 0 && form.usage.public_forms.length === 0 ? <p className="hint">Rattaché à rien pour l'instant.</p> : (
            <ul>
              {users.map((w) => <li key={w.slug}>Workflow <strong>{w.slug}</strong> — {w.versions.length ? `versions publiées : ${w.versions.join(", ")}` : "pas encore publié"}</li>)}
              {form.usage.public_forms.map((p) => <li key={p.id}>Formulaire public <strong>{p.name}</strong> (workflow {p.workflow})</li>)}
            </ul>
          )}
          <p className="hint">Ils gardent la version rattachée tant que vous ne les mettez pas à jour : publier ne change rien pour eux.</p>
        </section>
        <fieldset className="field"><legend>Type de changement{diff && !chosen ? " (suggéré d'après les changements)" : ""}</legend>
          {KINDS.map((k) => <label key={k.id} className="grp-opt"><input type="radio" name="fp-kind" value={k.id} checked={bump === k.id} onChange={() => setChosen(k.id)} /> <span><strong>{k.label}</strong> <span className="hint">— {k.hint}</span></span></label>)}
        </fieldset>
        <div className="field"><label htmlFor="fp-log">Notes de version (facultatif)</label><input id="fp-log" value={changelog} onChange={(e) => setChangelog(e.target.value)} maxLength={300} /></div>
        {go.error && <p role="alert" className="field-error">Erreur : {errorText(go.error)}</p>}
      </form>
    </Overlay>
  );
}

function Copy({ form, asTemplate, onClose, onDone }: { form: FormFull; asTemplate: boolean; onClose: () => void; onDone: (slug: string) => void }) {
  const { workspace } = useApp();
  const [name, setName] = useState(asTemplate ? `Modèle : ${form.name}` : `${form.name} (copie)`);
  const go = useMutation<FormCard, ApiError, void>({
    mutationFn: () => api(`/workspaces/${workspace.slug}/form-library/${form.slug}/duplicate/`, { json: { slug: slugify(name) || "copie", name, as_template: asTemplate } }),
    onSuccess: (f) => onDone(f.slug),
  });
  const title = asTemplate ? "Enregistrer comme modèle" : "Dupliquer le formulaire";
  return (
    <Overlay variant="modal" title={title} description={asTemplate ? "Le modèle est une copie du brouillon, réutilisable pour créer d'autres formulaires." : "Une copie du brouillon, indépendante de l'original."} onClose={onClose}
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="submit" form="fc-form" className="primary" disabled={!name.trim() || go.isPending}>{asTemplate ? "Créer le modèle" : "Dupliquer"}</button></>}>
      <form id="fc-form" onSubmit={(e: FormEvent) => { e.preventDefault(); if (name.trim()) go.mutate(); }}>
        <div className="field"><label htmlFor="fc-name">Nom</label><input id="fc-name" value={name} onChange={(e) => setName(e.target.value)} autoFocus /></div>
        {go.error && <p role="alert" className="field-error">Erreur : {go.error.status === 400 ? "cet identifiant existe déjà, changez le nom." : errorText(go.error)}</p>}
      </form>
    </Overlay>
  );
}

function Delete({ form, onClose, onDone }: { form: FormFull; onClose: () => void; onDone: () => void }) {
  const { workspace } = useApp();
  const del = useMutation<unknown, ApiError, void>({ mutationFn: () => api(`/workspaces/${workspace.slug}/form-library/${form.slug}/`, { method: "DELETE" }), onSuccess: onDone });
  return (
    <Confirm title={`Supprimer « ${form.name} » ?`} confirmLabel="Supprimer" busy={del.isPending} onConfirm={() => del.mutate()} onCancel={onClose}>
      {del.error ? <span role="alert" className="field-error">{errorText(del.error)}</span> : "Le brouillon et toutes les versions publiées seront supprimés. Impossible si le formulaire est rattaché à un workflow ou à un déclencheur."}
    </Confirm>
  );
}
