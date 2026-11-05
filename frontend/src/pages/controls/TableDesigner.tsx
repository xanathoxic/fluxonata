import { useState } from "react";
import { HelpTip } from "../../components/HelpTip";
import { ControlInput } from "../../forms/ControlInput";
import type { ControlSpec } from "../../forms/controls";
import { FormIcon, IconButton, IconLabel } from "../../forms/FormIcon";
import { slugify } from "../../forms/library";
import { FIELD_TYPES, isBlock, type FieldType, type FormField } from "../../forms/types";
import { Card, Num, Switch } from "./DesignerUi";

/** What a cell of a table may hold. */
const CELL_TYPES: FieldType[] = ["text", "textarea", "number", "date", "email", "select", "combobox", "radio", "checkbox"];
const CHOICES: FieldType[] = ["select", "combobox", "radio"];
const MAX_COLUMNS = 20;
const typeLabel = (t: FieldType) => FIELD_TYPES.find((f) => f.type === t)?.label ?? t;

/** A key made of the label, free among the others (the key is what the workflow receives: it stays when the label changes). */
function freeKey(label: string, used: Set<string>): string {
  const base = slugify(label).replace(/-/g, "_").replace(/^(\d)/, "c$1") || "colonne";
  let key = base;
  for (let n = 2; used.has(key); n++) key = `${base}_${n}`;
  return key;
}

interface Props {
  cfg: Omit<ControlSpec, "parts">;
  parts: FormField[];
  edit: boolean;
  onParts: (parts: FormField[]) => void;
  onTotals: (totals: string[] | undefined) => void;
  onAdvanced: () => void;
}

/** The columns of a table: a strip of headers (select one to set it up, move it, remove it), the settings of the selected column, and the table
 * as the person will fill it. A column is a field of the line, so the control is the same JSON as with the full editor (kept one click away
 * for conditions and rules). */
export function TableDesigner({ cfg, parts, edit, onParts, onTotals, onAdvanced }: Props) {
  const columns = parts.filter((p) => !isBlock(p.type));
  const [selected, setSelected] = useState<string | null>(columns[0]?.key ?? null);
  const current = columns.find((c) => c.key === selected) ?? columns[0];
  const totals = cfg.totals ?? [];
  const used = new Set(parts.map((p) => p.key));

  const update = (key: string, patch: Partial<FormField>) => {
    const next = parts.map((p) => {
      if (p.key !== key) return p;
      const merged = { ...p, ...patch } as Record<string, unknown>;
      for (const k of Object.keys(merged)) if (merged[k] === undefined || merged[k] === "") delete merged[k];
      return merged as unknown as FormField;
    });
    onParts(next);
  };
  const add = () => {
    const label = `Colonne ${columns.length + 1}`;
    const key = freeKey(label, used);
    onParts([...parts, { key, label, type: "text" }]);
    setSelected(key);
  };
  const move = (key: string, by: -1 | 1) => {
    const i = parts.findIndex((p) => p.key === key);
    const j = i + by;
    if (i < 0 || j < 0 || j >= parts.length) return;
    const next = [...parts];
    [next[i], next[j]] = [next[j] as FormField, next[i] as FormField];
    onParts(next);
  };
  const remove = (key: string) => {
    const next = parts.filter((p) => p.key !== key);
    onParts(next);
    if (totals.includes(key)) onTotals(totals.filter((k) => k !== key).length ? totals.filter((k) => k !== key) : undefined);
    setSelected(next.find((p) => !isBlock(p.type))?.key ?? null);
  };
  const retype = (c: FormField, type: FieldType) => {
    const { options: _o, compute: _c, min: _mn, max: _mx, ...rest } = c;
    update(c.key, { ...rest, type, ...(CHOICES.includes(type) ? { options: c.options?.length ? c.options : [{ value: "option-1", label: "Option 1" }] } : {}) } as Partial<FormField>);
    if (type !== "number" && totals.includes(c.key)) onTotals(totals.filter((k) => k !== c.key).length ? totals.filter((k) => k !== c.key) : undefined);
  };
  const others = columns.filter((c) => c.key !== current?.key).map((c) => `{${c.key}}`);

  const preview: FormField = { key: "apercu", label: "Aperçu", type: "custom", control: { ref: "apercu", version: "0", spec: { ...cfg, parts, display: "table" } as ControlSpec } };

  return (
    <div className="td">
      <div className="td-strip" role="toolbar" aria-label="Colonnes du tableau">
        <ol className="td-cols">
          {columns.map((c, i) => (
            <li key={c.key}>
              <button type="button" className={`td-col${current?.key === c.key ? " on" : ""}`} aria-pressed={current?.key === c.key} onClick={() => setSelected(c.key)}>
                <FormIcon name={c.type} size={16} /><span className="td-col-t">{c.label || c.key}</span>
                {c.required && <span className="td-req" title="Obligatoire" aria-label="obligatoire">*</span>}
                {totals.includes(c.key) && <span className="td-tot" title="Total sous la colonne" aria-label="avec total">Σ</span>}
                <span className="sr-only"> (colonne {i + 1} sur {columns.length})</span>
              </button>
            </li>
          ))}
        </ol>
        {edit && <button type="button" onClick={add} disabled={columns.length >= MAX_COLUMNS}><IconLabel icon="plus">Colonne</IconLabel></button>}
      </div>

      {current ? (
        <Card icon={current.type} title={`Colonne « ${current.label || current.key} »`}>
          <div className="td-bar">
            <IconButton icon="up" label="Déplacer à gauche" disabled={!edit || columns[0]?.key === current.key} onClick={() => move(current.key, -1)} />
            <IconButton icon="down" label="Déplacer à droite" disabled={!edit || columns[columns.length - 1]?.key === current.key} onClick={() => move(current.key, 1)} />
            <IconButton icon="trash" danger label="Supprimer la colonne" disabled={!edit} onClick={() => remove(current.key)} />
          </div>
          <div className="td-grid">
            <div className="field"><label htmlFor="td-label">Titre de la colonne</label><input id="td-label" value={current.label} maxLength={120} onChange={(e) => update(current.key, { label: e.target.value })} /></div>
            <div className="field"><label htmlFor="td-type">Contrôle de la cellule</label>
              <select id="td-type" value={current.type} onChange={(e) => retype(current, e.target.value as FieldType)}>
                {CELL_TYPES.map((t) => <option key={t} value={t}>{typeLabel(t)}</option>)}
                {!CELL_TYPES.includes(current.type) && <option value={current.type}>{typeLabel(current.type)}</option>}
              </select>
            </div>
            {current.type === "number" && (
              <div className="td-pair">
                <Num id="td-min" label="Minimum" value={current.min} max={1e9} onChange={(v) => update(current.key, { min: v })} />
                <Num id="td-max" label="Maximum" value={current.max} max={1e9} onChange={(v) => update(current.key, { max: v })} />
              </div>
            )}
            {(current.type === "text" || current.type === "textarea" || current.type === "email" || current.type === "number") && (
              <div className="field"><label htmlFor="td-ph">Texte d'exemple</label><input id="td-ph" value={current.placeholder ?? ""} maxLength={120} onChange={(e) => update(current.key, { placeholder: e.target.value })} /></div>
            )}
          </div>
          <div className="td-switches">
            <Switch id="td-req" checked={Boolean(current.required)} onChange={(v) => update(current.key, { required: v ? true : undefined })} label="Obligatoire" />
            {current.type === "number" && (
              <Switch id="td-tot" checked={totals.includes(current.key)} label="Total sous la colonne" help="Affiche la somme des valeurs de cette colonne sous le tableau."
                onChange={(on) => { const t = totals.filter((k) => k !== current.key); if (on) t.push(current.key); onTotals(t.length ? t : undefined); }} />
            )}
          </div>
          {CHOICES.includes(current.type) && (
            <fieldset className="td-options"><legend>Choix proposés dans la cellule</legend>
              {(current.options ?? []).map((o, i) => (
                <div key={i} className="td-opt">
                  <input aria-label={`Choix ${i + 1}`} value={o.label ?? o.value} maxLength={200}
                    onChange={(e) => update(current.key, { options: (current.options ?? []).map((x, k) => (k === i ? { value: x.value, label: e.target.value } : x)) })} />
                  <IconButton icon="trash" danger label={`Supprimer le choix ${i + 1}`} disabled={(current.options ?? []).length <= 1} onClick={() => update(current.key, { options: (current.options ?? []).filter((_, k) => k !== i) })} />
                </div>
              ))}
              <button type="button" onClick={() => { const list = current.options ?? []; update(current.key, { options: [...list, { value: freeKey(`choix ${list.length + 1}`, new Set(list.map((x) => x.value))), label: `Choix ${list.length + 1}` }] }); }}><IconLabel icon="plus">Ajouter un choix</IconLabel></button>
            </fieldset>
          )}
          {(current.type === "number" || current.type === "text") && (
            <div className="field"><label htmlFor="td-calc">Formule (colonne calculée) <HelpTip label="Aide : formule">Laissez vide pour une saisie libre. Sinon la cellule est calculée à partir des autres colonnes de la ligne, par exemple <code>{"{quantite} * {prix}"}</code>.</HelpTip></label>
              <input id="td-calc" value={current.compute ?? ""} maxLength={500} placeholder={others.length >= 2 ? `${others[0]} * ${others[1]}` : "{quantite} * {prix}"} onChange={(e) => update(current.key, { compute: e.target.value })} /></div>
          )}
          <p className="hint">Identifiant remis au workflow : <code>{current.key}</code> (ne change pas quand on renomme la colonne).</p>
        </Card>
      ) : (
        <p className="td-empty" role="note"><FormIcon name="datagrid" size={18} /> Aucune colonne. Ajoutez-en une pour commencer le tableau.</p>
      )}

      <Card icon="eye" title="Aperçu du tableau" help="Le tableau tel que la personne le remplira.">
        <ControlInput field={preview} id="td-preview" value={[{}, {}]} disabled onChange={() => undefined} />
      </Card>
      <p className="td-adv">
        <button type="button" onClick={onAdvanced}><IconLabel icon="design">Éditeur complet des champs</IconLabel></button>
        <span className="hint"> Pour les conditions, les règles et les réglages avancés d'une colonne.</span>
      </p>
    </div>
  );
}
