import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ApiError } from "../../api";
import { Badge, Confirm } from "../../components/AdminUi";
import { EditorShell } from "../../components/EditorShell";
import { Menu } from "../../components/Menu";
import { Overlay } from "../../components/Overlay";
import { PageHeader } from "../../components/PageHeader";
import { PageTabs } from "../../components/PageTabs";
import { StatusLine } from "../../components/StatusLine";
import { useAutosave } from "../../components/useAutosave";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { errorText } from "../../lib";
import { ControlInput } from "../../forms/ControlInput";
import { controlIcon, processControl, rowDefinition, type ControlFull, type ControlItem, type ControlSpec } from "../../forms/controls";
import { FormIcon, IconLabel } from "../../forms/FormIcon";
import type { FormDefinition, FormField } from "../../forms/types";
import type { LookupFn } from "../../forms/DefinitionForm";
import "../../forms/forms.css";
import { TestTab } from "./TestTab";
import { Switch } from "./DesignerUi";
import { ControlDesigner, type Cfg, type Meta } from "./ControlDesigner";

const split = (spec: ControlSpec): { cfg: Cfg; row: FormDefinition } => { const { parts, ...cfg } = spec; return { cfg, row: rowDefinition(parts) }; };
const join = (cfg: Cfg, row: FormDefinition): ControlSpec => ({ ...cfg, parts: (row.sections[0]?.fields ?? []) as FormField[] });
const TABS = ["design", "preview", "versions", "usage"] as const; // « test » (old address) opens « Aperçu & test » with the developer mode on
const DEV_KEY = "fluxonata.control.devmode";

/** One custom control: its identity, its kind and options, and its sub-fields designed with the form editor. Publishing creates an
 * immutable version, which the form editor then offers in its palette. */
export function ControlPage() {
  const { slug = "" } = useParams();
  return <ControlEditor key={slug} slug={slug} />; // one instance per control: what is pending is saved for THIS control when leaving
}

interface Content { meta: Meta; spec: ControlSpec }

function ControlEditor({ slug }: { slug: string }) {
  const { tab = "design" } = useParams();
  const [dev, setDev] = useState(() => { try { return tab === "test" || localStorage.getItem(DEV_KEY) === "on"; } catch { return tab === "test"; } });
  const setDevMode = (v: boolean) => { setDev(v); try { localStorage.setItem(DEV_KEY, v ? "on" : "off"); } catch { /* preference only */ } };
  const { workspace, can } = useApp();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const edit = can("edit");
  const key = ["control-lib", workspace.slug, slug];
  const detail = useQuery({ queryKey: key, queryFn: () => api<ControlFull>(`/workspaces/${workspace.slug}/control-library/${slug}/`) });
  const [meta, setMeta] = useState<Meta | null>(null);
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [row, setRow] = useState<FormDefinition | null>(null);
  const [baseline, setBaseline] = useState<{ value: Content; revision: number } | null>(null);
  const [dialog, setDialog] = useState<null | "publish" | "delete">(null);
  const [problems, setProblems] = useState<string[] | null>(null);

  const load = (d: ControlFull) => {
    const s = split(d.spec);
    const m = { name: d.name, description: d.description, icon: d.icon, folder: d.folder ?? "", tags: d.tags };
    setCfg(s.cfg); setRow(s.row); setMeta(m);
    setBaseline({ value: { meta: m, spec: join(s.cfg, s.row) }, revision: d.draft_revision });
  };
  useEffect(() => { if (detail.data && row === null) load(detail.data); }, [detail.data, row]); // eslint-disable-line react-hooks/exhaustive-deps

  const spec = useMemo(() => (cfg && row ? join(cfg, row) : null), [cfg, row]);
  // validity, as the server sees it (debounced)
  useEffect(() => {
    if (!edit || !spec) return;
    const timer = window.setTimeout(() => {
      api<{ problems: Record<string, string> }>(`/workspaces/${workspace.slug}/control-library/check/`, { json: { spec } })
        .then((r) => setProblems(Object.entries(r.problems).map(([k, v]) => `${k.replace(/^spec\.?/, "") || "contrôle"} : ${v}`)))
        .catch(() => setProblems(null));
    }, 600);
    return () => window.clearTimeout(timer);
  }, [spec, edit, workspace.slug]);

  const refresh = () => { void qc.invalidateQueries({ queryKey: key }); void qc.invalidateQueries({ queryKey: ["control-library", workspace.slug] }); void qc.invalidateQueries({ queryKey: ["control-library-published"] }); };
  const content = useMemo<Content | null>(() => (meta && spec ? { meta, spec } : null), [meta, spec]);
  const auto = useAutosave<Content>({
    storageKey: `fluxonata.draft.control.${workspace.slug}.${slug}`, value: content, baseline, enabled: edit, paused: dialog === "publish",
    save: async (v, revision) => {
      const r = await api<{ draft_revision: number }>(`/workspaces/${workspace.slug}/control-library/${slug}/`, { method: "PATCH", json: { ...v.meta, spec: v.spec, revision } });
      void qc.invalidateQueries({ queryKey: ["control-library", workspace.slug] }); void qc.invalidateQueries({ queryKey: ["control-library-published"] });
      return { revision: r.draft_revision };
    },
    onRecover: (v) => { const x = split(v.spec); setCfg(x.cfg); setRow(x.row); setMeta(v.meta); },
  });
  const reload = async () => { const r = await detail.refetch(); if (r.data) { auto.discardLocal(); load(r.data); } };
  const change = <T,>(set: (v: T) => void) => (v: T) => set(v);
  const d = detail.data;

  if (detail.isError) return <><PageHeader title="Contrôle introuvable" /><p><Link to="/library/controls">Retour aux contrôles</Link></p></>;
  if (!d || !cfg || !row || !meta || !spec) return <p role="status">Chargement…</p>;
  const active = tab === "test" ? "preview" : (TABS as readonly string[]).includes(tab) ? tab : "design";
  const bad = problems?.length ?? 0;
  const used = d.usage.length;
  return (
    <EditorShell backTo="/library/controls" backLabel="Retour aux contrôles" title={meta.name} titleHint={meta.description || undefined}
      icon={<span className="es-tile" aria-hidden="true"><FormIcon name={controlIcon(meta.icon)} size={20} /></span>}
      status={<StatusLine published={d.latest_version ? `v${d.latest_version.version} publiée` : "Jamais publié"} state={auto.state} savedAt={auto.savedAt} problems={problems === null ? null : bad} />}
      actions={edit && (
        <>
          {can("publish") && <button type="button" className="primary" onClick={() => setDialog("publish")} aria-haspopup="dialog"><IconLabel icon="send">Publier</IconLabel></button>}
          <Menu label="Plus" ariaLabel="Plus d'actions" align="end" entries={[{ label: "Supprimer", danger: true, onSelect: () => setDialog("delete") }]} />
        </>
      )}
      tabs={<PageTabs label="Contrôle" active={active} onChange={(t) => navigate(`/library/controls/${slug}/${t}`)} tabs={[
        { id: "design", label: "Conception", icon: "design", badge: bad || undefined, badgeTone: "alert" }, { id: "preview", label: "Aperçu & test", icon: "eye" },
        { id: "versions", label: "Versions", icon: "layers", badge: d.versions.length || undefined }, { id: "usage", label: "Utilisation", icon: "link", badge: used || undefined },
      ]} />}
      notices={<>
        {auto.state === "conflict" && <p role="alert" className="conflict">Ce contrôle a été modifié ailleurs. <button type="button" onClick={() => void reload()}>Recharger la version du serveur</button> <button type="button" onClick={() => void auto.overwrite()}>Écraser avec ma version</button></p>}
        {auto.state === "invalid" && <p role="alert" className="field-error">Le serveur refuse ce brouillon : {auto.message || "contenu invalide"}. Vos modifications restent dans ce navigateur ; corrigez-les, l'enregistrement reprendra tout seul.</p>}
        {auto.recover && <p role="alert" className="conflict">Des modifications non enregistrées ont été retrouvées dans ce navigateur. <button type="button" onClick={auto.acceptRecovery}>Les récupérer</button> <button type="button" onClick={auto.discardRecovery}>Les ignorer</button></p>}
        {bad > 0 && <ul className="fb-problems" aria-label="Problèmes du contrôle">{(problems ?? []).slice(0, 4).map((p, i) => <li key={i}>{p}</li>)}{bad > 4 && <li>… et {bad - 4} autre(s)</li>}</ul>}
      </>}>
      <div role="tabpanel" aria-label={active} className="tabpanel">
        {active === "design" && <ControlDesigner meta={meta} cfg={cfg} row={row} edit={edit} problems={problems} onMeta={change(setMeta)} onCfg={change(setCfg)} onRow={change(setRow)} />}
        {active === "preview" && (
          <>
            <Preview name={meta.name} slug={slug} spec={spec} />
            <div className="cp-dev"><Switch id="cp-dev" checked={dev} onChange={setDevMode} label="Mode développeur" help="Envoyer du JSON au contrôle comme le ferait un formulaire ou un service : options à proposer, valeur à pré-remplir, et voir ce que le workflow recevrait." /></div>
            {dev && <TestTab name={meta.name} slug={slug} spec={spec} />}
          </>
        )}
        {active === "versions" && <Versions control={d} edit={edit} onRestore={(s) => { const x = split(s); setCfg(x.cfg); setRow(x.row); navigate(`/library/controls/${slug}/design`); }} />}
        {active === "usage" && <Usage control={d} />}
      </div>
      {dialog === "publish" && <Publish control={d} dirty={auto.dirty} problems={bad} saveFirst={async () => { if (!(await auto.flush())) throw new Error("Le brouillon n'a pas pu être enregistré : corrigez-le ou résolvez le conflit avant de publier."); }} onClose={() => setDialog(null)} onDone={() => { setDialog(null); refresh(); }} />}
      {dialog === "delete" && (
        <Confirm title={`Supprimer « ${d.name} » ?`} confirmLabel="Supprimer" onConfirm={() => { void api(`/workspaces/${workspace.slug}/control-library/${slug}/`, { method: "DELETE" }).then(() => { refresh(); navigate("/library/controls"); }); }} onCancel={() => setDialog(null)}>
          Les formulaires qui l'utilisent gardent leur copie de la version employée ; il ne sera plus proposé dans la palette.
        </Confirm>
      )}
    </EditorShell>
  );
}

/** The control as a person will use it, and the value the server would keep. */
function Preview({ name, slug, spec }: { name: string; slug: string; spec: ControlSpec }) {
  const [value, setValue] = useState<unknown>(undefined);
  // a control that receives its options from the forms using it: try it with options typed here (each form reads its own service)
  const dynamic = Boolean(spec.dynamic);
  const [required, setRequired] = useState(false);
  const [sample, setSample] = useState("");
  const extra = useMemo<{ items: ControlItem[]; error: string }>(() => {
    if (!dynamic || !sample.trim()) return { items: [], error: "" };
    try {
      const v: unknown = JSON.parse(sample);
      if (!Array.isArray(v) || !v.every((o) => o && typeof o === "object" && typeof (o as ControlItem).value === "string")) return { items: [], error: "Une liste d'objets avec une « value » est attendue." };
      return { items: v as ControlItem[], error: "" };
    } catch { return { items: [], error: "JSON invalide." }; }
  }, [dynamic, sample]);
  const lookup = useMemo<LookupFn | undefined>(() => (dynamic && extra.items.length ? async () => extra.items : undefined), [dynamic, extra]);
  const field: FormField = useMemo(() => ({ key: "apercu", label: name, type: "custom", required, ...(dynamic && extra.items.length ? { source: { connection: "exemple", path: "/" } } : {}), control: { ref: slug, version: "0.0.0", name, spec }, ...(spec.mode === "items" && spec.items_editable ? { options: spec.items ?? [] } : {}) }), [name, slug, spec, required, dynamic, extra]);
  const result = processControl(field, value === undefined ? (spec.mode === "group" ? {} : spec.mode === "choice" ? undefined : []) : value);
  return (
    <section className="fb-panel fb-scroll ctl-preview" aria-label="Aperçu du contrôle">
      <p className="hint">Essayez le contrôle comme dans un formulaire : règles, calculs et contrôles de ses sous-champs s'appliquent. Rien n'est enregistré.</p>
      <div className="field field-inline"><input id="cp-req" type="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} /><label htmlFor="cp-req">Champ obligatoire dans le formulaire</label></div>
      <div className="field fb-field"><span className="fb-label" id="fb-apercu-l">{name}{required && <span aria-hidden="true" className="fb-req"> *</span>}</span>
        <ControlInput field={field} id="fb-apercu" value={value} lookup={lookup} error={result.problem || undefined} onChange={setValue} /></div>
      {dynamic && (
        <div className="field"><label htmlFor="cp-sample">Options d'exemple fournies par un formulaire (JSON)</label>
          <textarea id="cp-sample" className="mono" rows={5} value={sample} onChange={(e) => setSample(e.target.value)} placeholder={'[ { "value": "ford", "label": "Ford", "group": "US Brands" }, { "value": "plymouth", "label": "Plymouth", "disabled": true } ]'} />
          {extra.error ? <p role="alert" className="field-error">{extra.error}</p> : <p className="hint">Ce que chaque formulaire peut fournir : « value », « label », « group », « disabled ». Chaque formulaire configure sa propre lecture du service (réglages du champ).</p>}</div>
      )}
      {result.problem ? <p role="status" className="field-error">{result.problem}</p> : <p role="status" className="hint">Valeur acceptée.</p>}
      <h3>Valeur remise au workflow</h3>
      <pre className="ctl-json" aria-label="Valeur produite">{JSON.stringify(result.value ?? null, null, 2)}</pre>
    </section>
  );
}

function Versions({ control, edit, onRestore }: { control: ControlFull; edit: boolean; onRestore: (s: ControlSpec) => void }) {
  const { workspace } = useApp();
  const restore = useMutation<{ spec: ControlSpec }, ApiError, string>({ mutationFn: (v) => api(`/workspaces/${workspace.slug}/control-library/${control.slug}/versions/${v}/`), onSuccess: (r) => onRestore(r.spec) });
  return (
    <section className="fb-panel fb-scroll" aria-label="Versions du contrôle">
      <h2>Versions publiées</h2>
      {control.versions.length === 0 ? <p className="hint">Aucune version publiée : le contrôle n'est pas encore proposé dans l'éditeur de formulaires.</p> : (
        <ul className="ctl-vers">
          {control.versions.map((v) => (
            <li key={v.version}><Badge tone="info">v{v.version}</Badge> <span className="hint">{formatDate(v.created_at)}</span> {v.changelog && <span>{v.changelog}</span>}
              {edit && <button type="button" className="rl-small" onClick={() => restore.mutate(v.version)} disabled={restore.isPending}><IconLabel icon="undo">Reprendre dans le brouillon</IconLabel></button>}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Usage({ control }: { control: ControlFull }) {
  return (
    <section className="fb-panel fb-scroll" aria-label="Utilisation du contrôle">
      <h2>Formulaires qui l'utilisent</h2>
      {control.usage.length === 0 ? <p className="hint">Aucun formulaire de la bibliothèque n'utilise ce contrôle.</p> : <ul>{control.usage.map((s) => <li key={s}><Link to={`/library/forms/${s}`}>{s}</Link></li>)}</ul>}
    </section>
  );
}

function Publish({ control, dirty, problems, saveFirst, onClose, onDone }: { control: ControlFull; dirty: boolean; problems: number; saveFirst: () => Promise<unknown>; onClose: () => void; onDone: () => void }) {
  const { workspace } = useApp();
  const [changelog, setChangelog] = useState("");
  const [bump, setBump] = useState("minor");
  const go = useMutation<unknown, ApiError, void>({
    mutationFn: async () => { if (dirty) await saveFirst(); return api(`/workspaces/${workspace.slug}/control-library/${control.slug}/publish/`, { json: { changelog, bump } }); },
    onSuccess: onDone,
  });
  return (
    <Overlay variant="modal" title="Publier une version" description={`Le brouillon devient une version immuable, proposée dans la palette de l'éditeur de formulaires.${dirty ? " Il sera d'abord enregistré." : ""}`} onClose={onClose}
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="submit" form="cp-form" className="primary" disabled={go.isPending || problems > 0}>Publier</button></>}>
      <form id="cp-form" onSubmit={(e: FormEvent) => { e.preventDefault(); go.mutate(); }}>
        {problems > 0 && <p role="alert" className="field-error">{problems} problème(s) à corriger avant de publier.</p>}
        <div className="field"><label htmlFor="cp-bump">Type de changement</label>
          <select id="cp-bump" value={bump} onChange={(e) => setBump(e.target.value)}>
            <option value="patch">Correction (x.y.Z)</option><option value="minor">Évolution (x.Y.0)</option><option value="major">Changement important (X.0.0)</option>
          </select></div>
        <div className="field"><label htmlFor="cp-log">Notes de version (facultatif)</label><input id="cp-log" value={changelog} onChange={(e) => setChangelog(e.target.value)} maxLength={300} /></div>
        {go.error && <p role="alert" className="field-error">Erreur : {errorText(go.error)}</p>}
      </form>
    </Overlay>
  );
}
