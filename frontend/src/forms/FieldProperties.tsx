import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useApp } from "../context";
import { COMPUTABLE } from "./calc";
import { controlIcon, fieldForControl, MODES, producesOf, type ControlCard } from "./controls";
import { connectionLabel } from "./serviceParams";
import { ChoiceSourceOverlay, ServiceOverlay } from "./ServiceOverlay";
import { FormIcon, IconLabel, type FormIconName } from "./FormIcon";
import { describeEffect, sentence } from "./rules";
import { RulesOverlay } from "./rules/RulesOverlay";
import { CHOICE_TYPES, FIELD_TYPES, PART_TYPES, PRODUCES, isBlock, type DataFieldType, type DataSource, type FieldOption, type FieldType, type FormField, type GridColumn } from "./types";

export interface InputInfo { name: string; type: string; required: boolean }

const optionsToText = (o?: FieldOption[]) => (o ?? []).map((x) => (x.label && x.label !== x.value ? `${x.value} | ${x.label}` : x.value)).join("\n");
function textToOptions(t: string): FieldOption[] {
  return t.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const [value, ...rest] = l.split("|");
    const label = rest.join("|").trim();
    return { value: (value ?? "").trim(), ...(label ? { label } : {}) };
  });
}
const columnsToText = (c?: GridColumn[]) => (c ?? []).map((x) => `${x.key} | ${x.label ?? ""} | ${x.type ?? "text"}`).join("\n");
function textToColumns(t: string): GridColumn[] {
  return t.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const [key = "", label = "", type = "text"] = l.split("|").map((p) => p.trim());
    return { key, ...(label ? { label } : {}), type: type === "number" ? "number" : "text" };
  });
}

interface Props { field: FormField; earlier: FormField[]; inputs: InputInfo[]; connections: string[]; onChange: (f: FormField) => void; /** a sub-field of a custom control: no workflow input, no dynamic list */ part?: boolean }

/** The properties of one field: what it asks, how it is bound to the workflow, its choices and when it is shown. */
export function FieldProperties({ field, earlier, inputs, connections, onChange, part }: Props) {
  const { workspace } = useApp();
  const published = useQuery({ queryKey: ["control-library-published", workspace.slug], enabled: field.type === "custom", queryFn: () => api<{ results: ControlCard[] }>(`/workspaces/${workspace.slug}/control-library/?published=1`) });
  const [rulesOpen, setRulesOpen] = useState<false | "rules" | "calc">(false);
  const [serviceOpen, setServiceOpen] = useState(false);
  const [choiceOpen, setChoiceOpen] = useState(false);
  const set = (patch: Partial<FormField>) => onChange({ ...field, ...patch });
  const isChoice = CHOICE_TYPES.includes(field.type);
  const dynamic = field.source !== undefined;
  const input = inputs.find((i) => i.name === field.key);
  const produced = field.type === "custom" && field.control ? producesOf(field.control.spec) : PRODUCES[field.type as DataFieldType];
  const mismatch = !isBlock(field.type) && input && input.type !== produced && !(input.type === "integer" && field.type === "number");
  const setType = (type: FieldType) => {
    const next: FormField = { ...field, type };
    delete next.min; delete next.max;
    if (!CHOICE_TYPES.includes(type)) { delete next.options; delete next.source; } else if (!next.options && !next.source) next.options = [{ value: "option-1", label: "Option 1" }];
    if (type === "datagrid") next.columns = field.columns ?? [{ key: "colonne1", label: "Colonne 1", type: "text" }]; else delete next.columns;
    onChange(next);
  };
  const upgrade = field.type === "custom" && field.control ? (published.data?.results ?? []).find((c) => c.slug === field.control?.ref && c.published && c.published.version !== field.control.version) : undefined;
  const src: DataSource = field.source ?? { connection: "", path: "/" };
  const label = (key: string) => earlier.find((f) => f.key === key)?.label || key;
  const canCalc = (COMPUTABLE as string[]).includes(field.type);
  const count = (field.rules?.length ?? 0) + (field.visible_if && !field.rules?.length ? 1 : 0);
  const rule = (
    <fieldset>
      <legend><FormIcon name="rule" size={14} /> {canCalc ? "Conditions, règles et calcul" : "Conditions et règles"}</legend>
      {(field.rules ?? []).slice(0, 3).map((r, i) => <p key={r.id ?? i} className="fb-rsum" title={`Quand ${sentence(r.when, label)} → ${r.then.map(describeEffect).join(", ")}`}><strong>{i + 1}.</strong> {r.name || `Quand ${sentence(r.when, label)}`}</p>)}
      {(field.rules?.length ?? 0) > 3 && <p className="hint">… et {(field.rules?.length ?? 0) - 3} autre(s)</p>}
      {field.visible_if && !field.rules?.length && <p className="fb-rsum">Règle d'affichage : <code>{field.visible_if}</code></p>}
      {field.compute && <p className="fb-rsum" title="Valeur calculée à partir des autres champs : elle n'est pas modifiable"><strong>Calcul :</strong> <code>{field.compute}</code></p>}
      {count === 0 && <p className="hint">Aucune condition : le champ est toujours affiché.</p>}
      <button type="button" className="primary fp-rules-btn" onClick={() => setRulesOpen("rules")} aria-haspopup="dialog"><IconLabel icon="rule">{count ? `Gérer les conditions (${count})` : field.compute ? "Gérer les conditions et le calcul" : canCalc ? "Ajouter des conditions ou un calcul" : "Ajouter des conditions"}</IconLabel></button>
      {rulesOpen && <RulesOverlay field={field} earlier={earlier} tab={rulesOpen} onApply={onChange} onClose={() => setRulesOpen(false)} />}
    </fieldset>
  );
  if (isBlock(field.type)) {
    return (
      <div>
        {field.type === "heading" && <div className="field"><label htmlFor="fp-label">Texte du titre</label><input id="fp-label" value={field.label} onChange={(e) => set({ label: e.target.value })} maxLength={160} /></div>}
        {field.type === "paragraph" && <div className="field"><label htmlFor="fp-label">Texte</label><textarea id="fp-label" rows={5} value={field.label} onChange={(e) => set({ label: e.target.value })} maxLength={2000} /><p className="hint">Du texte simple : aucune mise en forme n'est interprétée.</p></div>}
        {field.type === "heading" && (
          <div className="field"><label htmlFor="fp-level">Niveau du titre</label>
            <select id="fp-level" value={field.level ?? 3} onChange={(e) => set({ level: Number(e.target.value) as 2 | 3 | 4 })}><option value={2}>Grand</option><option value={3}>Moyen</option><option value={4}>Petit</option></select></div>
        )}
        {field.type === "divider" && <p className="hint">Un trait horizontal pour séparer deux parties.</p>}
        {rule}
      </div>
    );
  }
  return (
    <div className="fp">
      <Sec id="content" title="Contenu" icon="design">
      <div className="field"><label htmlFor="fp-label">Libellé</label><input id="fp-label" value={field.label} onChange={(e) => set({ label: e.target.value })} /></div>
      <div className="field">
        <label htmlFor="fp-key">{part ? "Clé du sous-champ" : "Entrée du workflow"}</label>
        {inputs.length > 0 ? (
          <select id="fp-key" value={field.key} onChange={(e) => set({ key: e.target.value })}>
            {!input && <option value={field.key}>{field.key} (inconnue)</option>}
            {inputs.map((i) => <option key={i.name} value={i.name}>{i.name} ({i.type}{i.required ? ", obligatoire" : ""})</option>)}
          </select>
        ) : <input id="fp-key" value={field.key} onChange={(e) => set({ key: e.target.value })} />}
        {!input && inputs.length > 0 && <p className="hint field-error" role="status">Le workflow n'a pas d'entrée « {field.key} » : la publication sera refusée.</p>}
        {mismatch && <p className="hint field-error" role="status">Ce champ produit « {produced} », le workflow attend « {input?.type} ».</p>}
      </div>
      <div className="field">
        <label htmlFor="fp-type">{field.type === "custom" ? "Contrôle" : "Type de champ"}</label>
        {field.type === "custom" && field.control ? (
          <p id="fp-type" className="fb-rsum"><FormIcon name={controlIcon(field.control.icon)} size={14} /> <strong>{field.control.name ?? field.control.ref}</strong> <span className="hint">v{field.control.version} · {MODES.find((m) => m.id === field.control?.spec.mode)?.label}</span></p>
        ) : (
          <select id="fp-type" value={field.type} onChange={(e) => setType(e.target.value as FieldType)}>{FIELD_TYPES.filter((t) => t.group !== "content" && (!part || PART_TYPES.includes(t.type))).map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}</select>
        )}
        {upgrade && <button type="button" className="rl-small" onClick={() => onChange({ ...fieldForControl(upgrade as Required<Pick<ControlCard, "slug" | "published">>, field.key), label: field.label, required: field.required, help: field.help, layout: field.layout, rules: field.rules, visible_if: field.visible_if, ...(field.options && upgrade.published?.spec.items_editable ? { options: field.options } : {}), ...(field.source && upgrade.published?.spec.dynamic ? { source: field.source } : {}), ...(field.prefill ? { prefill: field.prefill } : {}) })}><IconLabel icon="redo">{`Passer à la version ${upgrade.published?.version}`}</IconLabel></button>}
      </div>
      <div className="field"><label htmlFor="fp-help">Aide sous le champ</label><input id="fp-help" value={field.help ?? ""} onChange={(e) => set({ help: e.target.value })} /></div>
      {["text", "textarea", "email", "number", "combobox"].includes(field.type) && <div className="field"><label htmlFor="fp-ph">Texte d'exemple</label><input id="fp-ph" value={field.placeholder ?? ""} onChange={(e) => set({ placeholder: e.target.value })} /></div>}
      {field.type === "checkbox" && <div className="field"><label htmlFor="fp-ph">Texte à côté de la case</label><input id="fp-ph" value={field.placeholder ?? ""} onChange={(e) => set({ placeholder: e.target.value })} /></div>}
      {field.type === "custom" && field.control?.spec.items_editable && (
        <fieldset>
          <legend>Choix de la liste</legend>
          <div className="field">
            <label htmlFor="fp-opts">Une option par ligne : valeur | libellé</label>
            <textarea id="fp-opts" rows={5} value={optionsToText(field.options)} onChange={(e) => set({ options: textToOptions(e.target.value) })} />
          </div>
        </fieldset>
      )}
      {field.type === "custom" && field.control && !part && (
        <fieldset>
          <legend><FormIcon name="link" size={14} /> Données depuis un service</legend>
          {field.source && <p className="fb-rsum" title={field.source.path}><strong>Options :</strong> {connectionLabel(field.source)} · <code>{field.source.path}</code></p>}
          {field.prefill && <p className="fb-rsum" title={field.prefill.path}><strong>Valeur préremplie :</strong> {field.prefill.connection || "(connexion ?)"} · <code>{field.prefill.path}</code></p>}
          {!field.source && !field.prefill && <p className="hint">Aucun service : {field.control.spec.dynamic ? "options fixes du contrôle, valeur saisie." : "la valeur est saisie."}</p>}
          <button type="button" onClick={() => setServiceOpen(true)} aria-haspopup="dialog"><IconLabel icon="link">{field.source || field.prefill ? "Gérer les données d'un service" : "Lire les données d'un service"}</IconLabel></button>
          {serviceOpen && <ServiceOverlay field={field} connections={connections} onApply={onChange} onClose={() => setServiceOpen(false)} />}
        </fieldset>
      )}
      {isChoice && (
        <fieldset>
          <legend>Choix proposés</legend>
          {!part && <div className="field field-inline"><input id="fp-dyn" type="checkbox" checked={dynamic} onChange={(e) => { const n = { ...field }; if (e.target.checked) { delete n.options; n.source = { connection: connections[0] ?? "", path: "/" }; } else { delete n.source; n.options = [{ value: "option-1", label: "Option 1" }]; } onChange(n); }} /><label htmlFor="fp-dyn">Liste dynamique (alimentée par un service)</label></div>}
          {!dynamic ? (
            <div className="field">
              <label htmlFor="fp-opts">Une option par ligne : valeur | libellé</label>
              <textarea id="fp-opts" rows={5} value={optionsToText(field.options)} onChange={(e) => set({ options: textToOptions(e.target.value) })} />
            </div>
          ) : (
            <>
              <p className="fb-rsum" title={src.path}><strong>Service :</strong> {connectionLabel(src)} · <code>{src.path}</code></p>
              <p className="hint">{[
                src.items_as === "entries" ? "clés d'un objet" : src.items_path ? `liste « ${src.items_path} »` : "réponse = liste",
                src.where ? "filtrée" : "", src.label_expr ? "libellé calculé" : "", src.sort ? "triée" : "",
                src.params?.length ? `lit ${src.params.join(", ")}` : "",
              ].filter(Boolean).join(" · ")}</p>
              <button type="button" onClick={() => setChoiceOpen(true)} aria-haspopup="dialog"><IconLabel icon="link">Configurer la liste dynamique</IconLabel></button>
              {choiceOpen && <ChoiceSourceOverlay field={field} connections={connections} onApply={onChange} onClose={() => setChoiceOpen(false)} />}
            </>
          )}
        </fieldset>
      )}
      {field.type === "datagrid" && (
        <div className="field">
          <label htmlFor="fp-cols">Colonnes, une par ligne : clé | libellé | text ou number</label>
          <textarea id="fp-cols" rows={4} value={columnsToText(field.columns)} onChange={(e) => set({ columns: textToColumns(e.target.value) })} />
        </div>
      )}
      </Sec>
      <Sec id="validation" title="Validation" icon="check">
      <div className="field field-inline"><input id="fp-req" type="checkbox" checked={Boolean(field.required)} onChange={(e) => set({ required: e.target.checked })} /><label htmlFor="fp-req">Obligatoire</label></div>
      {field.type === "number" && (
        <div className="fb-rule" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <div className="field"><label htmlFor="fp-min">Minimum</label><input id="fp-min" type="number" value={field.min ?? ""} onChange={(e) => set({ min: e.target.value === "" ? undefined : Number(e.target.value) })} /></div>
          <div className="field"><label htmlFor="fp-max">Maximum</label><input id="fp-max" type="number" value={field.max ?? ""} onChange={(e) => set({ max: e.target.value === "" ? undefined : Number(e.target.value) })} /></div>
        </div>
      )}
      </Sec>
      <Sec id="logic" title="Logique" icon="rule" badge={count || undefined}>
      {rule}
      </Sec>
    </div>
  );
}

const secKey = (id: string) => `fluxonata.fieldprops.${id}`;
/** A foldable group of properties (native <details>: keyboard and screen readers for free); open or closed is remembered per group. */
function Sec({ id, title, icon, badge, children }: { id: string; title: string; icon: FormIconName; badge?: number; children: React.ReactNode }) {
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(secKey(id)) !== "closed"; } catch { return true; } });
  return (
    <details className="fp-sec" open={open} onToggle={(e) => { const o = (e.currentTarget as HTMLDetailsElement).open; setOpen(o); try { localStorage.setItem(secKey(id), o ? "open" : "closed"); } catch { /* preference only */ } }}>
      <summary><FormIcon name={icon} size={16} /><span>{title}</span>{badge !== undefined && <span className="pt-badge">{badge}</span>}</summary>
      <div className="fp-body">{children}</div>
    </details>
  );
}
