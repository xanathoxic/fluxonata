import { FormIcon, type FormIconName } from "./FormIcon";
import type { FormStyle } from "./types";
import { useRadioGroup } from "../components/useRadioGroup";

type Display = NonNullable<FormStyle["display"]>;
export const DISPLAYS: { id: Display; label: string; icon: FormIconName; hint: string }[] = [
  { id: "standard", label: "Standard", icon: "width3", hint: "Une carte centrée, avec la grille de colonnes que vous dessinez." },
  { id: "page", label: "Pleine page", icon: "width4", hint: "Le formulaire prend toute la place disponible, sur toute la largeur." },
  {
    id: "modern",
    label: "Moderne",
    icon: "simple",
    hint: "Une seule colonne centrée, champs arrondis et aérés, dans l'esprit des formulaires Bootstrap récents.",
  },
];

/** Le type d'affichage du formulaire, en trois grandes cases à icône (le changement s'enregistre tout seul, comme le reste du brouillon). */
export function DisplayPicker({ value, onChange, disabled }: { value: FormStyle["display"]; onChange: (v: Display) => void; disabled?: boolean }) {
  const radio = useRadioGroup<HTMLDivElement>();
  const current = value ?? "standard";
  return (
    <div ref={radio.ref} onKeyDown={radio.onKeyDown} className="fb-displays" role="radiogroup" aria-label="Type d'affichage">
      {DISPLAYS.map((d) => (
        // biome-ignore lint/a11y/useSemanticElements: rôle explicite conservé ; l'élément natif changerait la mise en forme
        <button
          key={d.id}
          type="button"
          role="radio"
          aria-checked={current === d.id}
          disabled={disabled}
          className={`fb-display${current === d.id ? " on" : ""}`}
          onClick={() => onChange(d.id)}
        >
          <span className={`fb-dthumb fb-dthumb-${d.id}`} aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span className="fb-dname">
            <FormIcon name={d.icon} size={16} />
            {d.label}
          </span>
          <small>{d.hint}</small>
        </button>
      ))}
    </div>
  );
}

export const MAX_SIDE = 40;

/** Marges de chaque côté du formulaire moderne (gauche et droite liées), en % de la largeur disponible ; « Automatique » = la colonne par défaut. */
export function SideMargin({ value, onChange, disabled }: { value: number | undefined; onChange: (v: number | undefined) => void; disabled?: boolean }) {
  return (
    <div className="fb-side">
      <label htmlFor="fb-side">Marges latérales (gauche et droite liées)</label>
      <div className="fb-side-row">
        <FormIcon name="narrower" size={16} />
        <input
          id="fb-side"
          type="range"
          min={0}
          max={MAX_SIDE}
          step={1}
          disabled={disabled}
          value={value ?? 0}
          aria-valuetext={value === undefined ? "Automatique" : `${value} %`}
          onChange={(e) => onChange(Number(e.target.value))}
        />
        <FormIcon name="wider" size={16} />
        <output htmlFor="fb-side">{value === undefined ? "Auto" : `${value} %`}</output>
        <button type="button" disabled={disabled || value === undefined} onClick={() => onChange(undefined)}>
          Automatique
        </button>
      </div>
      <small className="hint">
        {value === undefined
          ? "Colonne centrée de largeur habituelle."
          : value === 0
            ? "Le formulaire occupe toute la largeur."
            : `Une marge de ${value} % de chaque côté : plus elle est petite, plus le formulaire est large.`}
      </small>
    </div>
  );
}
