import { type JsonSchema } from "../../api";
import { HelpTip } from "../../components/HelpTip";
import { FormIcon, IconLabel } from "../../forms/FormIcon";
import { autoMap, dataFields, problemsOf, produces, sourceOf, type InputMap } from "../../forms/inputMap";
import type { FormDefinition } from "../../forms/types";
import "./inputform.css";

const typeOf = (p: JsonSchema) => (Array.isArray(p.type) ? p.type.filter((t) => t !== "null").join(" ou ") : p.type ?? "texte");
const TYPE_FR: Record<string, string> = { string: "texte", number: "nombre", integer: "entier", boolean: "oui / non", array: "liste", object: "objet" };

/** Which field of the form feeds each input of the workflow: one line per input, « input → field », with what is wrong beside it. Automatic
 * (same names) until the person chooses; then every input has its own field, or none when it is optional. The server checks the same rules
 * when the workflow is published. */
export function InputMapping({ definition, schema, map, editable, onChange }: { definition: FormDefinition; schema: JsonSchema; map: InputMap | undefined; editable: boolean; onChange: (m: InputMap | undefined) => void }) {
  const fields = dataFields(definition);
  const inputs = Object.entries(schema.properties ?? {});
  const problems = problemsOf(definition, schema, map);
  const required = new Set(schema.required ?? []);
  const keys = new Set(fields.map((f) => f.key));
  const used = new Set(inputs.map(([name]) => sourceOf(map, name, keys)).filter((k): k is string => k !== null));
  const loose = fields.filter((f) => !used.has(f.key));
  const custom = Boolean(map);
  const choose = (name: string, key: string) => onChange({ ...(map ?? autoMap(definition, schema)), [name]: key });
  if (inputs.length === 0) return <p className="hint">Ce workflow n'a pas d'entrée : rien à faire correspondre.</p>;
  const extra = Object.entries(problems).filter(([k]) => !(k in (schema.properties ?? {})));
  return (
    <div className="im">
      <div className="im-mode" role="radiogroup" aria-label="Mode de correspondance">
        <label className={`im-opt${!custom ? " on" : ""}`}>
          <input type="radio" name="im-mode" checked={!custom} disabled={!editable} onChange={() => onChange(undefined)} />
          <FormIcon name="link" size={20} /><span><strong>Automatique</strong><span className="hint">Chaque entrée reçoit le champ qui porte le même nom.</span></span>
        </label>
        <label className={`im-opt${custom ? " on" : ""}`}>
          <input type="radio" name="im-mode" checked={custom} disabled={!editable} onChange={() => onChange(autoMap(definition, schema))} />
          <FormIcon name="sliders" size={20} /><span><strong>Personnalisée</strong><span className="hint">Vous choisissez le champ de chaque entrée.</span></span>
        </label>
      </div>

      <div className="im-bar">
        <h3>Une ligne par entrée du workflow <HelpTip label="Aide : correspondance">Un champ qui ne remplit aucune entrée sert seulement à la saisie (conditions, règles) : sa valeur n'est pas transmise au workflow. Une entrée facultative peut rester sans champ.</HelpTip></h3>
        {custom && editable && <button type="button" onClick={() => onChange(autoMap(definition, schema))}><IconLabel icon="wand">Associer par nom ou libellé</IconLabel></button>}
      </div>
      <ul className="im-list" aria-label="Entrées du workflow">
        {inputs.map(([name, prop]) => {
          const current = sourceOf(map, name, keys) ?? "";
          const f = fields.find((x) => x.key === current);
          const bad = problems[name];
          return (
            <li key={name} className={`im-row${bad ? " bad" : ""}`}>
              <div className="im-in">
                <span className="im-name">{prop.title ?? name}</span>
                <span className="im-meta"><code>{name}</code> · {TYPE_FR[typeOf(prop)] ?? typeOf(prop)}{required.has(name) && <span className="im-req">obligatoire</span>}</span>
              </div>
              <FormIcon name="right" size={18} />
              <div className="im-out">
                <label className="sr-only" htmlFor={`im-${name}`}>Champ pour l'entrée {name}</label>
                <select id={`im-${name}`} value={current} disabled={!editable || !custom} onChange={(e) => choose(name, e.target.value)}>
                  <option value="">{required.has(name) ? "— Aucun champ —" : "— Aucun champ (laissée vide) —"}</option>
                  {fields.map((x) => <option key={x.key} value={x.key}>{x.label || x.key} ({x.key}) · {TYPE_FR[produces(x)] ?? produces(x)}</option>)}
                </select>
              </div>
              <div className="im-state" role="status">
                {bad ? <span className="im-ko"><FormIcon name="warn" size={16} /> {bad}</span> : <span className="im-ok"><FormIcon name="check" size={16} /> {f ? "Correct" : "Laissée vide"}</span>}
              </div>
            </li>
          );
        })}
      </ul>
      {extra.map(([k, v]) => <p key={k} role="status" className="im-ko"><FormIcon name="warn" size={16} /> {v}</p>)}
      {custom && loose.length > 0 && <p className="hint im-loose"><FormIcon name="info" size={14} /> Champs qui ne remplissent aucune entrée (non transmis) : {loose.map((x) => x.label || x.key).join(", ")}.</p>}
    </div>
  );
}
