import { FormIcon, type FormIconName } from "../FormIcon";
import type { Effect, LoadKind, TextTarget } from "../rules";
import { isBlock, type FormField } from "../types";

export const EFFECT_KINDS: { id: Effect["do"]; label: string; icon: FormIconName }[] = [
  { id: "show", label: "Afficher le champ", icon: "eye" },
  { id: "hide", label: "Masquer le champ", icon: "eyeOff" },
  { id: "require", label: "Rendre obligatoire", icon: "check" },
  { id: "optional", label: "Rendre facultatif", icon: "close" },
  { id: "readonly", label: "Mettre en lecture seule", icon: "lock" },
  { id: "editable", label: "Rendre modifiable", icon: "edit" },
  { id: "text", label: "Changer un texte", icon: "paragraph" },
  { id: "fill", label: "Remplir le champ", icon: "edit" },
  { id: "options", label: "Limiter les choix", icon: "list2" },
  { id: "load", label: "Charger les données du service", icon: "link" },
];
const FILLABLE = ["text", "textarea", "email", "date", "number", "select", "radio", "combobox", "checkbox"];
const TARGETS: { id: TextTarget; label: string }[] = [{ id: "label", label: "Libellé" }, { id: "help", label: "Aide sous le champ" }, { id: "placeholder", label: "Texte d'exemple" }];

export function blankEffect(kind: Effect["do"], field: FormField): Effect {
  if (kind === "text") return { do: "text", target: "label", text: field.label ?? "" };
  if (kind === "fill") return { do: "fill", value: "" };
  if (kind === "options") return { do: "options", values: (field.options ?? []).map((o) => o.value) };
  if (kind === "load") return { do: "load", what: field.source ? "options" : "prefill" };
  return { do: kind };
}

/** Why an effect cannot be offered for this field (a block has no value, only a static list can be limited…). */
export function unavailable(kind: Effect["do"], field: FormField): string {
  if (isBlock(field.type) && (kind === "require" || kind === "optional")) return "Un bloc de contenu n'a pas de valeur.";
  if (isBlock(field.type) && (kind === "readonly" || kind === "editable")) return "Un bloc de contenu n'a pas de valeur à verrouiller.";
  if (kind === "fill" && !FILLABLE.includes(field.type)) return "Ce type de champ ne peut pas être rempli par une règle.";
  if (kind === "load" && !(field.type === "custom" && (field.source || field.prefill))) return "Ce champ ne lit pas de service : réglez d'abord « Données depuis un service » dans ses propriétés.";
  if (kind === "options" && !(field.options && !field.source && ["select", "radio", "combobox", "checkbox_list"].includes(field.type))) return "Seule une liste dont les options sont saisies dans le formulaire peut être limitée.";
  return "";
}

interface Props { effects: Effect[]; field: FormField; earlier: FormField[]; onChange: (e: Effect[]) => void; label: string }

/** The effects of a branch (« alors » or « sinon »): what the field becomes. */
export function EffectList({ effects, field, earlier, onChange, label }: Props) {
  const set = (i: number, e: Effect) => onChange(effects.map((x, k) => (k === i ? e : x)));
  return (
    <div className="rl-effects" role="group" aria-label={label}>
      {effects.length === 0 && <p className="hint">Aucun effet.</p>}
      {effects.map((e, i) => {
        const kind = EFFECT_KINDS.find((k) => k.id === e.do) as (typeof EFFECT_KINDS)[number];
        const n = `${label} : effet ${i + 1}`;
        return (
          <div key={i} className="rl-effect" role="group" aria-label={n}>
            <span className="rl-ekind"><FormIcon name={kind.icon} size={16} />{kind.label}</span>
            {e.do === "text" && (
              <>
                <select aria-label={`${n} : texte à changer`} value={e.target} onChange={(ev) => set(i, { ...e, target: ev.target.value as TextTarget })}>
                  {TARGETS.filter((t) => !isBlock(field.type) || t.id === "label").map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
                </select>
                <input aria-label={`${n} : nouveau texte`} value={e.text} maxLength={500} placeholder="Nouveau texte, avec des {champs}" onChange={(ev) => set(i, { ...e, text: ev.target.value })} />
                {earlier.length > 0 && (
                  <select aria-label={`${n} : insérer un champ`} value="" onChange={(ev) => ev.target.value && set(i, { ...e, text: `${e.text}{${ev.target.value}}` })}>
                    <option value="">Insérer {"{champ}"}…</option>
                    {earlier.map((f) => <option key={f.key} value={f.key}>{f.label || f.key}</option>)}
                  </select>
                )}
              </>
            )}
            {e.do === "fill" && (
              <>
                <input aria-label={`${n} : valeur à mettre`} value={e.value} maxLength={500} placeholder="Valeur, avec des {champs}" onChange={(ev) => set(i, { ...e, value: ev.target.value })} />
                {earlier.length > 0 && (
                  <select aria-label={`${n} : insérer un champ`} value="" onChange={(ev) => ev.target.value && set(i, { ...e, value: `${e.value}{${ev.target.value}}` })}>
                    <option value="">Insérer {"{champ}"}…</option>
                    {earlier.map((f) => <option key={f.key} value={f.key}>{f.label || f.key}</option>)}
                  </select>
                )}
              </>
            )}
            {e.do === "load" && (
              <select aria-label={`${n} : données à charger`} value={e.what} onChange={(ev) => set(i, { ...e, what: ev.target.value as LoadKind })}>
                {field.source && <option value="options">Les options</option>}
                {field.prefill && <option value="prefill">La valeur préremplie</option>}
                {!(field.source && e.what === "options") && !(field.prefill && e.what === "prefill") && <option value={e.what}>{e.what === "options" ? "Les options (service non réglé)" : "La valeur préremplie (service non réglé)"}</option>}
              </select>
            )}
            {e.do === "options" && (
              <span className="rl-chips" role="group" aria-label={`${n} : choix proposés`}>
                {(field.options ?? []).map((o) => {
                  const on = e.values.includes(o.value);
                  return <button key={o.value} type="button" aria-pressed={on} className={`rl-chip${on ? " on" : ""}`} onClick={() => set(i, { ...e, values: on ? e.values.filter((v) => v !== o.value) : [...e.values, o.value] })}>{o.label || o.value}</button>;
                })}
              </span>
            )}
            <button type="button" className="fb-ibtn fb-del rl-x" aria-label={`Supprimer ${n.toLowerCase()}`} title="Supprimer cet effet" onClick={() => onChange(effects.filter((_, k) => k !== i))}><FormIcon name="trash" size={14} /></button>
          </div>
        );
      })}
      <div className="rl-add" role="group" aria-label={`${label} : ajouter un effet`}>
        {EFFECT_KINDS.map((k) => {
          const why = unavailable(k.id, field);
          return <button key={k.id} type="button" className="rl-small" disabled={Boolean(why)} title={why || k.label} aria-label={`Ajouter : ${k.label}`} onClick={() => onChange([...effects, blankEffect(k.id, field)])}><FormIcon name={k.icon} size={14} /> {k.label}</button>;
        })}
      </div>
    </div>
  );
}
