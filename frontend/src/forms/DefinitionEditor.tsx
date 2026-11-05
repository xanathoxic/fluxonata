import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type JsonSchema } from "../api";
import { Confirm } from "../components/AdminUi";
import { useApp } from "../context";
import { DefinitionForm, type LookupFn } from "./DefinitionForm";
import { FieldProperties, type InputInfo } from "./FieldProperties";
import { FormIcon, IconButton, IconLabel, fieldIcon } from "./FormIcon";
import { LayoutCanvas } from "./LayoutCanvas";
import { readers } from "./rules";
import { append, COLS, MAX_HEIGHT, normalize, orderedFields, place, readingOrder } from "./layout";
import { CssTab } from "./CssTab";
import { ModeTabs } from "./ModeTabs";
import { SegmentedControl } from "../components/SegmentedControl";
import { StyleEditor } from "./StyleEditor";
import { controlIcon, fieldForControl, type ControlCard } from "./controls";
import { FIELD_TYPES, PART_TYPES, isBlock, type FieldType, type FormDefinition, type FormField, type FormSection, type FormStyle, type Layout } from "./types";
import "./forms.css";

interface Sel { s: number; key: string | null }
const move = <T,>(a: T[], i: number, d: -1 | 1): T[] => {
  const j = i + d;
  if (j < 0 || j >= a.length) return a;
  const out = [...a];
  [out[i], out[j]] = [out[j] as T, out[i] as T];
  return out;
};

export function inputsOf(schema: JsonSchema | undefined): InputInfo[] {
  const required = new Set(schema?.required ?? []);
  return Object.entries(schema?.properties ?? {}).map(([name, p]) => ({ name, type: String((p as JsonSchema).type ?? "string"), required: required.has(name) }));
}

/** A field per workflow input, with the type that fits it: the quickest way to a working form. */
export function fieldsFromInputs(schema: JsonSchema | undefined): FormField[] {
  const required = new Set(schema?.required ?? []);
  return Object.entries(schema?.properties ?? {}).map(([name, raw]) => {
    const p = raw as JsonSchema;
    const enumValues = Array.isArray((p as { enum?: unknown }).enum) ? ((p as { enum: unknown[] }).enum.map(String)) : null;
    const base = { key: name, label: p.title ?? name, required: required.has(name), ...(p.description ? { help: p.description } : {}) };
    if (enumValues) return { ...base, type: "select" as const, options: enumValues.map((value) => ({ value })) };
    if (p.type === "integer" || p.type === "number") return { ...base, type: "number" as const };
    if (p.type === "boolean") return { ...base, type: "checkbox" as const };
    if (p.type === "array") return { ...base, type: "checkbox_list" as const, options: [{ value: "option-1", label: "Option 1" }] };
    return { ...base, type: "text" as const };
  });
}


interface Props {
  def: FormDefinition;
  onChange: (d: FormDefinition) => void;
  edit: boolean;
  /** inputs of the workflow the fields are bound to */
  schema: JsonSchema | undefined;
  connections: string[];
  /** where the preview's dynamic lists come from; absent: the preview explains instead of loading */
  lookup?: LookupFn;
  /** extra controls for the toolbar (the form page puts its « reference inputs » choice there) */
  toolbarExtra?: React.ReactNode;
  /** designing the sub-fields of a custom control: one section, no workflow, no style (published controls may be nested) */
  control?: boolean;
}

const BLOCK_DEFAULTS: Record<string, Partial<FormField>> = {
  heading: { label: "Titre de la partie", level: 3 },
  paragraph: { label: "Texte d'explication à compléter." },
  divider: { label: "" },
};
const HISTORY = 100;

/** The form designer: a toolbar (undo, redo, add, views, validity), a palette, sections laid out on a 12-column grid, the
 * properties of a field, the style, the author's CSS and a live preview. It is shared by the form page and the workflow's
 * input-form tab; the caller keeps the definition and decides when it is saved. */
export function DefinitionEditor({ def, onChange, edit, schema, connections, lookup, toolbarExtra, control }: Props) {
  const { workspace } = useApp();
  const controls = useQuery({ queryKey: ["control-library-published", workspace.slug], queryFn: () => api<{ results: ControlCard[] }>(`/workspaces/${workspace.slug}/control-library/?published=1`) });
  const inputs = useMemo(() => inputsOf(schema), [schema]);
  const [sel, setSel] = useState<Sel>({ s: 0, key: null });
  const [tab, setTab] = useState("build");
  const [look, setLook] = useState<"simple" | "css">("simple");
  const [previewW, setPreviewW] = useState<"full" | "tablet" | "phone">("full");
  // under 768 px the designer is not usable: only the preview is offered (publication stays available from the page header)
  const [narrow, setNarrow] = useState(() => typeof window !== "undefined" && window.matchMedia("(max-width: 767px)").matches);
  useEffect(() => { const m = window.matchMedia("(max-width: 767px)"); const on = () => setNarrow(m.matches); m.addEventListener("change", on); return () => m.removeEventListener("change", on); }, []);
  const [preview, setPreview] = useState<Record<string, unknown>>({});
  const [confirm, setConfirm] = useState<null | { title: string; run: () => void }>(null);
  const [showPalette, setShowPalette] = useState(true);
  const [search, setSearch] = useState("");
  const [problems, setProblems] = useState<string[] | null>(null);

  // undo / redo: our own edits are remembered, a definition replaced from outside (reload) starts a new history
  const past = useRef<FormDefinition[]>([]);
  const future = useRef<FormDefinition[]>([]);
  const last = useRef(def);
  useEffect(() => { if (def !== last.current) { past.current = []; future.current = []; last.current = def; } }, [def]);
  const commit = (next: FormDefinition) => { past.current.push(def); if (past.current.length > HISTORY) past.current.shift(); future.current = []; last.current = next; onChange(next); };
  const undo = () => { const prev = past.current.pop(); if (prev) { future.current.push(def); last.current = prev; onChange(prev); } };
  const redo = () => { const next = future.current.pop(); if (next) { past.current.push(def); last.current = next; onChange(next); } };

  // validity, as the server sees it (debounced): the pill of the toolbar
  useEffect(() => {
    if (!edit || control) return;
    const timer = window.setTimeout(() => {
      api<{ definition?: Record<string, string> }>(`/workspaces/${workspace.slug}/form-library/check/`, { json: { definition: def } })
        .then((r) => setProblems(Object.entries(r.definition ?? {}).map(([k, v]) => `${k} : ${v}`)))
        .catch(() => setProblems(null));
    }, 700);
    return () => window.clearTimeout(timer);
  }, [def, edit, control, workspace.slug]);

  const si = Math.min(sel.s, def.sections.length - 1);
  const section = def.sections[si] as FormSection;
  const used = new Set(orderedFields(def).map((f) => f.key));
  const fields = readingOrder(section.fields);

  const patchSection = (i: number, patch: Partial<FormSection>) => commit({ ...def, sections: def.sections.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
  const setFields = (i: number, next: FormField[]) => patchSection(i, { fields: next });
  const current = sel.key ? fields.find((f) => f.key === sel.key) : undefined;
  // every other data field of the form, wherever it is laid out, except those that already read this one (that would be a loop)
  const earlier = (() => {
    if (!current) return [];
    const all = orderedFields(def);
    const loops = readers(all, current.key);
    return all.filter((f) => !isBlock(f.type) && f.key !== current.key && !loops.has(f.key));
  })();

  const freeKey = (prefix: string, start: number) => { let n = start; while (used.has(`${prefix}${n}`)) n++; return `${prefix}${n}`; };
  const addField = (type: FieldType) => {
    let f: FormField;
    if (isBlock(type)) f = { key: freeKey("bloc_", used.size + 1), type, ...BLOCK_DEFAULTS[type] } as FormField;
    else {
      const free = inputs.find((i) => !used.has(i.name));
      const key = free?.name ?? freeKey("champ_", used.size + 1);
      const label = FIELD_TYPES.find((t) => t.type === type)?.label ?? key;
      f = { key, label: free ? free.name : label, type, ...(type === "select" || type === "radio" || type === "combobox" || type === "checkbox_list" ? { options: [{ value: "option-1", label: "Option 1" }] } : {}), ...(type === "datagrid" ? { columns: [{ key: "colonne1", label: "Colonne 1", type: "text" as const }] } : {}) };
    }
    setFields(si, append(section.fields, f));
    setSel({ s: si, key: f.key });
  };
  const addControl = (c: ControlCard) => {
    if (!c.published) return;
    const f = fieldForControl({ slug: c.slug, published: c.published }, freeKey(`${c.slug.replace(/-/g, "_")}_`, 1));
    setFields(si, append(section.fields, f));
    setSel({ s: si, key: f.key });
  };
  const addSection = () => { commit({ ...def, sections: [...def.sections, { id: `s${Date.now().toString(36)}`, title: `Section ${def.sections.length + 1}`, fields: [] }] }); setSel({ s: def.sections.length, key: null }); };
  const removeSection = (i: number) => { commit({ ...def, sections: def.sections.filter((_, j) => j !== i) }); setSel({ s: 0, key: null }); };
  const removeField = (key: string) => { setFields(si, readingOrder(section.fields).filter((f) => f.key !== key)); setSel({ s: si, key: null }); };
  const placeField = (key: string, l: Layout) => setFields(si, place(section.fields, key, l));
  const fillFromInputs = () => {
    let next = section.fields;
    for (const f of fieldsFromInputs(schema).filter((x) => !used.has(x.key))) next = append(next, f);
    setFields(si, next);
  };
  const setStyle = (style: FormStyle | undefined) => { const { style: _old, ...rest } = def; commit(style ? { ...rest, style } : rest); };
  const setCss = (css: string | undefined) => { const { css: _old, ...rest } = def; commit(css ? { ...rest, css } : rest); };

  const toolbar = current && edit && (
    <div className="fb-toolbar" role="toolbar" aria-label={`Placer « ${current.label || current.key} »`}>
      <span className="fb-tgroup" role="group" aria-label="Déplacer">
      <IconButton icon="back" label="Décaler d'une colonne vers la gauche" onClick={() => placeField(current.key, { ...(current.layout as Layout), col: (current.layout as Layout).col - 1 })} />
      <IconButton icon="right" label="Décaler d'une colonne vers la droite" onClick={() => placeField(current.key, { ...(current.layout as Layout), col: (current.layout as Layout).col + 1 })} />
      <IconButton icon="up" label="Monter d'une ligne" onClick={() => placeField(current.key, { ...(current.layout as Layout), row: (current.layout as Layout).row - 1 })} disabled={(current.layout as Layout).row <= 1} />
      <IconButton icon="down" label="Descendre d'une ligne" onClick={() => placeField(current.key, { ...(current.layout as Layout), row: (current.layout as Layout).row + 1 })} />
      </span>
      <span className="fb-tgroup" role="group" aria-label="Redimensionner">
      <IconButton icon="narrower" label="Réduire d'une colonne" onClick={() => placeField(current.key, { ...(current.layout as Layout), span: (current.layout as Layout).span - 1 })} disabled={(current.layout as Layout).span <= 1} />
      <IconButton icon="taller" label="Plus haut d'une ligne" onClick={() => placeField(current.key, { ...(current.layout as Layout), height: ((current.layout as Layout).height ?? 1) + 1 })} disabled={((current.layout as Layout).height ?? 1) >= MAX_HEIGHT} />
      <IconButton icon="shorter" label="Moins haut d'une ligne" onClick={() => placeField(current.key, { ...(current.layout as Layout), height: ((current.layout as Layout).height ?? 1) - 1 })} disabled={((current.layout as Layout).height ?? 1) <= 1} />
      <IconButton icon="wider" label="Élargir d'une colonne" onClick={() => placeField(current.key, { ...(current.layout as Layout), span: (current.layout as Layout).span + 1 })} disabled={(current.layout as Layout).span >= COLS} />
      </span>
      <span className="fb-tgroup fb-tdel" role="group" aria-label="Supprimer">
      <IconButton icon="trash" danger label={`Supprimer le champ ${current.label || current.key}`} onClick={() => removeField(current.key)} />
      </span>
    </div>
  );

  const canvas = (
    <div className="fb-canvas">
      {(control ? def.sections.slice(0, 1) : def.sections).map((s, i) => (
        <section key={s.id ?? i} className="fb-panel" aria-label={control ? "Sous-champs du contrôle" : `Section ${i + 1}`}>
          {!control && <div className="fb-sec-head">
            <input aria-label={`Titre de la section ${i + 1}`} value={s.title} disabled={!edit} onFocus={() => setSel((p) => (p.s === i ? p : { s: i, key: null }))} onChange={(e) => patchSection(i, { title: e.target.value })} />
            {edit && (
              <span className="fb-tools">
                <IconButton icon="up" label={`Monter la section ${s.title}`} onClick={() => commit({ ...def, sections: move(def.sections, i, -1) })} disabled={i === 0} />
                <IconButton icon="down" label={`Descendre la section ${s.title}`} onClick={() => commit({ ...def, sections: move(def.sections, i, 1) })} disabled={i === def.sections.length - 1} />
                <IconButton icon="trash" danger label={`Supprimer la section ${s.title}`} onClick={() => setConfirm({ title: `Supprimer la section « ${s.title} » et ses ${s.fields.length} champ(s) ?`, run: () => removeSection(i) })} disabled={def.sections.length === 1} />
              </span>
            )}
          </div>}
          {!control && <input aria-label={`Texte d'introduction de la section ${i + 1}`} placeholder="Texte d'introduction (facultatif)" disabled={!edit} value={s.description ?? ""} onChange={(e) => patchSection(i, { description: e.target.value })} style={{ width: "100%", marginBottom: 8 }} />}
          {s.fields.length === 0 && <p className="hint">{control ? "Aucun sous-champ : cliquez sur un élément de la palette." : "Aucun champ : cliquez sur un élément de la palette."}</p>}
          {i === si && edit && <div className="fb-tslot">{toolbar || <p className="hint">Sélectionnez un champ pour le déplacer ou le redimensionner.</p>}</div>}
          <LayoutCanvas fields={readingOrder(s.fields)} selectedKey={i === si ? sel.key : null} edit={edit} label={`Grille de la section ${s.title}`}
            onSelect={(key) => setSel({ s: i, key })} onPlace={(key, l) => { setSel({ s: i, key }); setFields(i, place(s.fields, key, l)); }} />
        </section>
      ))}
      {edit && !control && <p><button type="button" onClick={addSection}><IconLabel icon="section">Ajouter une section</IconLabel></button></p>}
    </div>
  );

  const q = search.trim().toLowerCase();
  const rows = (group: "data" | "content") => FIELD_TYPES.filter((t) => (!control || PART_TYPES.includes(t.type)) && (t.group === "content") === (group === "content") && (!q || `${t.label} ${t.hint}`.toLowerCase().includes(q)));
  const palette = (
    <aside className="fb-pal" aria-label="Palette de champs">
      <div className="fb-psearch"><FormIcon name="search" size={16} /><input aria-label="Rechercher un élément" placeholder="Rechercher un élément" value={search} onChange={(e) => setSearch(e.target.value)} /></div>
      <p className="hint">Cliquez sur un élément pour l'ajouter à la section choisie.</p>
      {(["data", "content"] as const).map((g) => rows(g).length > 0 && (
        <div key={g}>
          <h3 className="fb-ph">{g === "data" ? "Champs" : "Contenu"}</h3>
          <ul className="fb-plist">{rows(g).map((t) => <li key={t.type}><button type="button" className={`fb-prow ${g}`} title={t.hint} onClick={() => addField(t.type)}><FormIcon name={fieldIcon(t.type)} /><span>{t.label}</span></button></li>)}</ul>
        </div>
      ))}
      {(controls.data?.results ?? []).filter((c) => !q || `${c.name} ${c.description}`.toLowerCase().includes(q)).length > 0 && (
        <div>
          <h3 className="fb-ph">Contrôles personnalisés</h3>
          <ul className="fb-plist">{(controls.data?.results ?? []).filter((c) => !q || `${c.name} ${c.description}`.toLowerCase().includes(q)).map((c) => <li key={c.slug}><button type="button" className="fb-prow custom" title={c.description || c.name} onClick={() => addControl(c)}><FormIcon name={controlIcon(c.published?.icon ?? c.icon)} /><span>{c.published?.name ?? c.name}</span></button></li>)}</ul>
        </div>
      )}
      {!q && inputs.length > 0 && (
        <div>
          <h3 className="fb-ph">Workflow</h3>
          <ul className="fb-plist"><li><button type="button" className="fb-prow wf" onClick={fillFromInputs} disabled={inputs.every((i) => used.has(i.name))}><FormIcon name="wand" /><span>Un champ par entrée</span></button></li></ul>
        </div>
      )}
      {q && rows("data").length + rows("content").length === 0 && <p className="hint">Aucun élément ne correspond.</p>}
    </aside>
  );

  const bad = problems?.length ?? 0;
  const pill = problems !== null && (
    <span className={`fb-pill ${bad ? "bad" : "ok"}`} role="status"><FormIcon name={bad ? "warn" : "check"} size={14} />{bad ? `${bad} problème(s)` : "Valide"}</span>
  );
  const tabs = narrow ? [{ id: "preview", label: "Aperçu", icon: "eye" as const }] : [
    { id: "build", label: "Éditeur", icon: "design" as const },
    ...(control ? [] : [{ id: "look", label: "Apparence", icon: "palette" as const }]),
    { id: "preview", label: "Aperçu", icon: "eye" as const },
  ];
  const sample = <DefinitionForm definition={def} values={preview} onChange={setPreview} lookup={lookup}><button type="button" className="primary">Envoyer</button></DefinitionForm>;
  return (
    <div className="fb-editor-fill">
      <div className="fb-bar" role="toolbar" aria-label="Outils de l'éditeur de formulaire">
        <span className="fb-grp">
          <IconButton icon="undo" label="Annuler" onClick={undo} disabled={!edit || past.current.length === 0} />
          <IconButton icon="redo" label="Rétablir" onClick={redo} disabled={!edit || future.current.length === 0} />
        </span>
        {tab === "build" && edit && <button type="button" className="fb-addbtn" aria-pressed={showPalette} onClick={() => setShowPalette(!showPalette)} title="Afficher ou masquer la palette"><IconLabel icon="plus">Ajouter</IconLabel></button>}
        <ModeTabs tabs={tabs} active={narrow ? "preview" : tab} onChange={setTab} label="Vue de l'éditeur" />
        <span className="fb-spacer" />
        {toolbarExtra}
        {pill}
      </div>
      {bad > 0 && <ul className="fb-problems" aria-label="Problèmes du formulaire">{(problems ?? []).slice(0, 4).map((p, i) => <li key={i}>{p}</li>)}{bad > 4 && <li>… et {bad - 4} autre(s)</li>}</ul>}
      {narrow && <p role="note" className="notice">Édition recommandée sur grand écran : sur un téléphone, vous pouvez consulter l'aperçu du formulaire. Ouvrez-le sur un ordinateur ou une tablette pour le modifier.</p>}
      <div className="fb-body">
      {!narrow && tab === "build" && (
        <div className={`fb-builder${showPalette && edit ? "" : " nopal"}`}>
          {showPalette && edit && palette}
          {canvas}
          <aside className="fb-panel" aria-label="Propriétés">
            <h2>Propriétés</h2>
            {current ? (
              <fieldset disabled={!edit} style={{ border: 0, padding: 0, margin: 0 }}>
                <FieldProperties key={`${si}-${fields.findIndex((x) => x.key === current.key)}`} field={current} earlier={earlier} inputs={inputs} connections={connections} part={control}
                  onChange={(f) => { setFields(si, normalize(section.fields).map((x) => (x.key === current.key ? f : x))); if (f.key !== current.key) setSel({ s: si, key: f.key }); }} />
              </fieldset>
            ) : <p className="hint">Sélectionnez un champ pour le régler : libellé, type, liaison au workflow, choix, placement, règle d'affichage. Glissez-le sur la grille pour le déplacer.</p>}
          </aside>
        </div>
      )}
      {!narrow && tab === "look" && (
        <div className="fb-look">
          <div className="cs-modes"><SegmentedControl label="Niveau de réglage de l'apparence" active={look} onChange={(v) => setLook(v as "simple" | "css")} segments={[{ id: "simple", label: "Simple", icon: "design" }, { id: "css", label: "Avancé (CSS)", icon: "braces" }]} /></div>
          {look === "simple" ? (
            <section className="fb-panel fb-stylepanel" aria-label="Style du formulaire">
              <StyleEditor style={def.style} onChange={setStyle} edit={edit} />
              <div className="fb-scroll"><h2 className="fb-prevh">Aperçu</h2>{sample}</div>
            </section>
          ) : <CssTab def={def} onCss={setCss} edit={edit} lookup={lookup} />}
        </div>
      )}
      {(narrow || tab === "preview") && (
        <section className="fb-panel fb-scroll" aria-label="Aperçu du formulaire">
          <p className="hint">Voici ce que verra la personne : placement, style, CSS et règles d'affichage s'appliquent. Rien n'est envoyé.</p>
          <div className="cs-modes"><SegmentedControl label="Largeur de l'aperçu" active={previewW} onChange={(v) => setPreviewW(v as "full" | "tablet" | "phone")} segments={[{ id: "full", label: "Bureau", icon: "layers" }, { id: "tablet", label: "Tablette 768", icon: "list" }, { id: "phone", label: "Mobile 390", icon: "phone" }]} /></div>
          <div className="fb-previewframe" data-width={previewW} style={{ maxWidth: previewW === "phone" ? 390 : previewW === "tablet" ? 768 : undefined }}>
            <DefinitionForm definition={def} values={preview} onChange={setPreview} lookup={lookup} />
          </div>
        </section>
      )}
      </div>
      {confirm && <Confirm title={confirm.title} confirmLabel="Confirmer" onConfirm={() => { confirm.run(); setConfirm(null); }} onCancel={() => setConfirm(null)}>Cette action modifie le formulaire.</Confirm>}
    </div>
  );
}
