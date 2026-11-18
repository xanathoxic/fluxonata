import { FormIcon } from "../FormIcon";
import type { Candidate } from "./pick";

interface Props {
  /** ce que l'élément survolé est, en clair (vide : rien sous la souris) */
  hover: string;
  /** ce qu'on peut viser à partir du dernier clic */
  candidates: Candidate[];
  /** le sélecteur actuel de la règle */
  selector: string;
  /** les niveaux de l'élément choisi, du plus extérieur au plus intérieur (vide : rien n'est encore choisi) */
  levels?: { label: string; title: string }[];
  /** le niveau actuellement visé */
  level?: number;
  onLevel?: (i: number) => void;
  onChoose: (selector: string) => void;
  onSelector: (selector: string) => void;
  disabled?: boolean;
}

const plural = (n: number) => `${n} élément${n > 1 ? "s" : ""}`;

/** La « cible libre » sans écrire de chemin : survoler l'aperçu nomme l'élément, cliquer propose ce qu'on peut viser (avec le nombre
 * d'éléments concernés) ; le chemin écrit reste visible et modifiable dans « Avancé ». */
export function PickPanel({ hover, candidates, selector, levels = [], level = 0, onLevel, onChoose, onSelector, disabled }: Props) {
  return (
    <div className="cs-pickp">
      <p className="cs-pickhint">
        <FormIcon name="pointer" size={16} /> Survolez l'aperçu : l'élément sous la souris est entouré. <strong>Cliquez</strong> pour le choisir.
      </p>
      <p className="cs-hover" aria-live="polite">
        {hover ? (
          <>
            <FormIcon name="eye" size={14} /> {hover}
          </>
        ) : (
          <span className="hint">Rien sous la souris</span>
        )}
      </p>
      {levels.length > 0 && (
        <div className="cs-levels">
          <nav className="cs-crumbs" aria-label="Niveaux de l'élément visé">
            {levels.map((l, i) => (
              <button
                // biome-ignore lint/suspicious/noArrayIndexKey: liste affichée en entier, sans réordonnancement ; la position EST le niveau
                key={`${i}-${l.label}`}
                type="button"
                className={`cs-crumb${i === level ? " on" : ""}`}
                aria-current={i === level ? "true" : undefined}
                title={l.title}
                disabled={disabled}
                onClick={() => onLevel?.(i)}
              >
                {l.label}
              </button>
            ))}
          </nav>
          <span className="cs-levelbtns">
            <button type="button" disabled={disabled || level <= 0} onClick={() => onLevel?.(level - 1)} title="Viser l'élément qui contient celui-ci">
              <FormIcon name="up" size={14} /> Parent
            </button>
            <button
              type="button"
              disabled={disabled || level >= levels.length - 1}
              onClick={() => onLevel?.(level + 1)}
              title="Redescendre vers l'élément cliqué"
            >
              <FormIcon name="down" size={14} /> Enfant
            </button>
          </span>
        </div>
      )}
      {candidates.length > 0 && (
        <div role="radiogroup" aria-label="Que voulez-vous viser ?" className="cs-cands">
          <p className="cs-mh">Que voulez-vous viser ?</p>
          {candidates.map((c) => (
            // biome-ignore lint/a11y/useSemanticElements: rôle explicite conservé ; l'élément natif changerait la mise en forme
            <button
              key={c.id}
              type="button"
              role="radio"
              aria-checked={selector === c.selector}
              disabled={disabled}
              className={`cs-cand${selector === c.selector ? " on" : ""}`}
              onClick={() => onChoose(c.selector)}
            >
              <FormIcon name={selector === c.selector ? "check" : "pointer"} size={16} />
              <span>{c.label}</span>
              <small>{plural(c.count)}</small>
            </button>
          ))}
        </div>
      )}
      <details className="cs-manual">
        <summary>
          <FormIcon name="code" size={14} /> Chemin de la cible (avancé)
        </summary>
        <input
          className="cs-text"
          aria-label="Chemin de la cible"
          placeholder='[data-key="nom"] input'
          value={selector}
          disabled={disabled}
          onChange={(e) => onSelector(e.target.value)}
        />
        <p className="hint">Écrit automatiquement quand vous cliquez ; modifiez-le seulement si vous savez ce que vous faites.</p>
      </details>
    </div>
  );
}
