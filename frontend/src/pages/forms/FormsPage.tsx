import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, apiAll, type ApiError, type WorkflowSummary } from "../../api";
import { Badge, Confirm, EmptyRow, SearchBox, SortTh, usePaged, useSort } from "../../components/AdminUi";
import { FolderTree, inFolder } from "../../components/FolderTree";
import { Overlay } from "../../components/Overlay";
import { PageHeader } from "../../components/PageHeader";
import { Tabs } from "../../components/Tabs";
import { TagFilter, TagInput, TagList } from "../../components/Tags";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { errorText } from "../../lib";
import { fieldsFromInputs } from "../../forms/DefinitionEditor";
import { FormIcon, IconLabel } from "../../forms/FormIcon";
import { slugify, type BuiltinTemplate, type FormCard } from "../../forms/library";
import { emptyDefinition, type FormDefinition } from "../../forms/types";

type Source = "blank" | "template" | "workflow";

/** « Formulaires » : the library of forms (and of templates to copy), attached afterwards to workflows and public forms. */
export function FormsPage() {
  const { workspace, can } = useApp();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [tab, setTab] = useState("forms");
  const [folder, setFolder] = useState("");
  const [q, setQ] = useState("");
  const [tagF, setTagF] = useState("");
  const [creating, setCreating] = useState(false);
  const [toDelete, setToDelete] = useState<FormCard | null>(null);
  const list = useQuery({ queryKey: ["form-library", workspace.slug], refetchOnMount: "always", queryFn: () => api<{ results: FormCard[] }>(`/workspaces/${workspace.slug}/form-library/`) });
  const all = list.data?.results ?? [];
  const wantTemplates = tab === "templates";
  const shown = useMemo(() => all.filter((f) => f.is_template === wantTemplates && inFolder(f.folder, folder) && (!tagF || f.tags.includes(tagF)) && (!q || `${f.name} ${f.description} ${f.tags.join(" ")}`.toLowerCase().includes(q.toLowerCase()))), [all, wantTemplates, folder, tagF, q]);
  const { sorted, sort, toggle } = useSort(shown, { name: (f) => f.name, folder: (f) => f.folder, updated: (f) => Date.parse(f.updated_at) || 0 }, { key: "updated", dir: "desc" });
  const paged = usePaged(sorted, 25);
  const del = useMutation<unknown, ApiError, string>({
    mutationFn: (slug) => api(`/workspaces/${workspace.slug}/form-library/${slug}/`, { method: "DELETE" }),
    onSuccess: () => { setToDelete(null); void qc.invalidateQueries({ queryKey: ["form-library"] }); },
  });
  const dup = useMutation<FormCard, ApiError, { from: FormCard }>({
    mutationFn: ({ from }) => api(`/workspaces/${workspace.slug}/form-library/${from.slug}/duplicate/`, { json: { slug: slugify(`${from.name} copie ${Date.now().toString().slice(-4)}`), name: `${from.name} (copie)` } }),
    onSuccess: (f) => { void qc.invalidateQueries({ queryKey: ["form-library"] }); navigate(`/library/forms/${f.slug}`); },
  });
  const forms = all.filter((f) => !f.is_template);
  const table = (
    <div className="table-wrap">
      <table className="adm-table">
        <caption className="sr-only">{wantTemplates ? "Modèles de formulaire" : "Formulaires"}</caption>
        <thead><tr><SortTh label="Nom" field="name" sort={sort} onSort={toggle} /><SortTh label="Dossier" field="folder" sort={sort} onSort={toggle} /><th scope="col">Étiquettes</th><th scope="col">Version</th><th scope="col">Champs</th><SortTh label="Modifié" field="updated" sort={sort} onSort={toggle} /><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>
          {paged.slice.map((f) => (
            <tr key={f.slug}>
              <td><Link className="lst-title" to={`/library/forms/${f.slug}`}><FormIcon name="design" size={16} /> {f.name}</Link>{f.description && <span className="lst-sub">{f.description}</span>}</td>
              <td>{f.folder ? <span className="tag">{f.folder}</span> : "—"}</td>
              <td>{f.tags.length > 0 ? <TagList tags={f.tags} onPick={setTagF} active={tagF} /> : "—"}</td>
              <td>{f.is_template ? <Badge tone="info">modèle</Badge> : f.latest_version ? <Badge tone="info">v{f.latest_version.version}</Badge> : <Badge tone="draft">brouillon</Badge>}</td>
              <td>{f.fields}</td>
              <td>{formatDate(f.updated_at)}</td>
              <td className="row-actions">
                <Link className="icon-btn" to={`/library/forms/${f.slug}`} aria-label={`${can("edit") ? "Modifier" : "Consulter"} ${f.name}`} title={can("edit") ? "Modifier" : "Consulter"}><FormIcon name={can("edit") ? "edit" : "eye"} /></Link>
                {can("edit") && <button type="button" className="icon-btn neutral-icon" onClick={() => dup.mutate({ from: f })} aria-label={`Dupliquer ${f.name}`} title={f.is_template ? "Créer un formulaire à partir de ce modèle" : "Dupliquer"}><FormIcon name={f.is_template ? "wand" : "copy"} /></button>}
                {can("edit") && <button type="button" className="icon-btn danger-icon" onClick={() => setToDelete(f)} aria-label={`Supprimer ${f.name}`} title="Supprimer"><FormIcon name="trash" /></button>}
              </td>
            </tr>
          ))}
          {shown.length === 0 && <EmptyRow cols={7}>{list.isLoading ? "Chargement…" : wantTemplates ? "Aucun modèle de l'espace. Enregistrez un formulaire comme modèle pour le réutiliser." : <>Aucun formulaire ici. {can("edit") ? "Créez-en un pour le rattacher à un workflow ou à un formulaire public." : ""}</>}</EmptyRow>}
        </tbody>
      </table>
    </div>
  );
  return (
    <>
      <PageHeader title="Formulaires" description={`${forms.length} formulaire(s) et ${all.length - forms.length} modèle(s) dans cet espace. Un formulaire se conçoit ici, puis se rattache à un workflow, à un déclencheur public ou à une action.`} />
      <div className="wf-list-layout">
        <FolderTree label="Dossiers de formulaires" kind="form" canEdit={can("edit")} folders={all.map((f) => f.folder)} current={folder} onPick={setFolder} />
        <section className="adm-card lst-main">
          <Tabs label="Formulaires et modèles" active={tab} onChange={setTab} tabs={[
            { id: "forms", label: <IconLabel icon="design">Formulaires</IconLabel>, content: null },
            { id: "templates", label: <IconLabel icon="section">Modèles</IconLabel>, content: null },
          ]} />
          <div className="adm-filters">
            <SearchBox label="Rechercher un formulaire" value={q} onChange={setQ} placeholder="Nom, description ou étiquette" />
            <TagFilter lists={all.map((f) => f.tags)} value={tagF} onChange={setTagF} />
            <span className="lst-count" role="status">{shown.length} résultat(s){folder ? ` dans « ${folder} »` : ""}</span>
            <span style={{ flex: 1 }} />
            {can("edit") && <button type="button" className="primary" aria-haspopup="dialog" onClick={() => setCreating(true)}><IconLabel icon="plus">Nouveau formulaire</IconLabel></button>}
          </div>
          {list.isError && <p role="alert">Une erreur est survenue. Réessayez.</p>}
          {table}
          {paged.pager("Pagination des formulaires")}
        </section>
      </div>
      {creating && <NewForm folder={folder} existing={all} onClose={() => setCreating(false)} onCreated={(slug) => { void qc.invalidateQueries({ queryKey: ["form-library"] }); navigate(`/library/forms/${slug}`); }} />}
      {toDelete && (
        <Confirm title={`Supprimer « ${toDelete.name} » ?`} confirmLabel="Supprimer" busy={del.isPending} onConfirm={() => del.mutate(toDelete.slug)} onCancel={() => setToDelete(null)}>
          {del.error ? <span role="alert" className="field-error">{errorText(del.error)}</span> : "Ses versions publiées seront supprimées aussi. Un formulaire rattaché à un workflow ou à un déclencheur ne peut pas être supprimé."}
        </Confirm>
      )}
    </>
  );
}

function NewForm({ folder, existing, onClose, onCreated }: { folder: string; existing: FormCard[]; onClose: () => void; onCreated: (slug: string) => void }) {
  const { workspace } = useApp();
  const [name, setName] = useState("");
  const [where, setWhere] = useState(folder);
  const [tags, setTags] = useState<string[]>([]);
  const [source, setSource] = useState<Source>("blank");
  const [tplId, setTplId] = useState("");
  const [wf, setWf] = useState("");
  const builtin = useQuery({ queryKey: ["form-builtin", workspace.slug], queryFn: () => api<{ results: BuiltinTemplate[] }>(`/workspaces/${workspace.slug}/form-library/builtin-templates/`) });
  const wfs = useQuery({ queryKey: ["workflows", workspace.slug], queryFn: () => apiAll<WorkflowSummary>(`/workspaces/${workspace.slug}/workflows/`) });
  const own = existing.filter((f) => f.is_template);
  const withInputs = (wfs.data?.results ?? []).filter((w) => w.latest_version && Object.keys(w.latest_version.input_schema.properties ?? {}).length > 0);
  const create = useMutation<FormCard, ApiError, void>({
    mutationFn: async () => {
      const base = `/workspaces/${workspace.slug}/form-library`;
      const slug = slugify(name) || "formulaire";
      if (source === "template" && tplId.startsWith("own:")) return api<FormCard>(`${base}/${tplId.slice(4)}/duplicate/`, { json: { slug, name, folder: where, as_template: false } });
      let definition: FormDefinition = emptyDefinition();
      if (source === "template") definition = builtin.data?.results.find((t) => t.id === tplId)?.definition ?? definition;
      if (source === "workflow") {
        const schema = withInputs.find((w) => w.slug === wf)?.latest_version?.input_schema;
        definition = { version: 1, sections: [{ id: "s1", title: "Informations", fields: fieldsFromInputs(schema) }] };
      }
      return api<FormCard>(`${base}/`, { json: { slug, name, folder: where, tags, definition } });
    },
    onSuccess: (f) => onCreated(f.slug),
  });
  const ready = name.trim() && (source === "blank" || (source === "template" && tplId) || (source === "workflow" && wf));
  const err = create.error;
  return (
    <Overlay variant="modal" title="Nouveau formulaire" description="Partez de zéro, d'un modèle ou des entrées d'un workflow." onClose={onClose}
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="submit" form="nf-form" className="primary" disabled={!ready || create.isPending}>Créer et ouvrir le concepteur</button></>}>
      <form id="nf-form" onSubmit={(e: FormEvent) => { e.preventDefault(); if (ready) create.mutate(); }}>
        <div className="field"><label htmlFor="nf-name">Nom du formulaire</label><input id="nf-name" value={name} onChange={(e) => setName(e.target.value)} autoFocus /></div>
        <div className="field"><label htmlFor="nf-folder">Dossier (facultatif)</label><input id="nf-folder" value={where} onChange={(e) => setWhere(e.target.value)} placeholder="ex. RH/Demandes" /></div>
        <TagInput value={tags} onChange={setTags} suggestions={[...new Set(existing.flatMap((f) => f.tags))].sort()} />
        <fieldset>
          <legend>Point de départ</legend>
          <div className="field field-inline"><input id="nf-s1" type="radio" name="nf-src" checked={source === "blank"} onChange={() => setSource("blank")} /><label htmlFor="nf-s1">Un formulaire vide</label></div>
          <div className="field field-inline"><input id="nf-s2" type="radio" name="nf-src" checked={source === "template"} onChange={() => setSource("template")} /><label htmlFor="nf-s2">Un modèle</label></div>
          {source === "template" && (
            <div className="field"><label htmlFor="nf-tpl">Modèle</label>
              <select id="nf-tpl" value={tplId} onChange={(e) => setTplId(e.target.value)}>
                <option value="">Choisir…</option>
                <optgroup label="Modèles fournis">{(builtin.data?.results ?? []).map((t) => <option key={t.id} value={t.id}>{t.name} — {t.description}</option>)}</optgroup>
                {own.length > 0 && <optgroup label="Modèles de l'espace">{own.map((t) => <option key={t.slug} value={`own:${t.slug}`}>{t.name}</option>)}</optgroup>}
              </select>
            </div>
          )}
          <div className="field field-inline"><input id="nf-s3" type="radio" name="nf-src" checked={source === "workflow"} onChange={() => setSource("workflow")} /><label htmlFor="nf-s3">Les entrées d'un workflow</label></div>
          {source === "workflow" && (
            <div className="field"><label htmlFor="nf-wf">Workflow</label>
              <select id="nf-wf" value={wf} onChange={(e) => setWf(e.target.value)}><option value="">Choisir…</option>{withInputs.map((w) => <option key={w.slug} value={w.slug}>{w.name}</option>)}</select>
            </div>
          )}
        </fieldset>
        {err && <p role="alert" className="field-error">Erreur : {err.status === 400 ? "cet identifiant existe déjà, changez le nom." : errorText(err)}</p>}
      </form>
    </Overlay>
  );
}
