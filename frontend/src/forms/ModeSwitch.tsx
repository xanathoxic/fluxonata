import { FormIcon, type FormIconName } from "./FormIcon";

export type FormMode = "simple" | "steps" | "design";
const MODES: { id: FormMode; label: string; icon: FormIconName; hint: string }[] = [
  { id: "simple", label: "Simple", icon: "simple", hint: "Toutes les entrées sur une page" },
  { id: "steps", label: "Par étapes", icon: "steps", hint: "Un assistant en étapes numérotées" },
  { id: "design", label: "Concepteur", icon: "design", hint: "Sections, règles d'affichage, listes dynamiques" },
];

/** Choice of the input form's kind, as three icon buttons (native radios: arrow keys move, the name is the text). */
export function ModeSwitch({ mode, onChange, disabled }: { mode: FormMode; onChange: (m: FormMode) => void; disabled?: boolean }) {
  return (
    <div role="radiogroup" aria-label="Type de formulaire d'entrée" className="fb-modes">
      {MODES.map((m) => (
        <label key={m.id} className={`fb-mode${mode === m.id ? " on" : ""}${disabled ? " disabled" : ""}`} title={m.hint}>
          <input type="radio" name="form-mode" value={m.id} checked={mode === m.id} disabled={disabled} onChange={() => onChange(m.id)} />
          <FormIcon name={m.icon} />{m.label}
        </label>
      ))}
    </div>
  );
}
