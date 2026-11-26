import { useEffect, useState } from "react";
import { parseVars, varsText } from "./inventory";

/** Un champ de variables au format JSON : il garde son texte tant qu'il est invalide et ne prévient la page que lorsqu'il est lisible. */
export function VarsField({
  id,
  label,
  value,
  onChange,
  disabled,
}: {
  id: string;
  label: string;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  disabled?: boolean;
}) {
  const [text, setText] = useState(varsText(value));
  const [error, setError] = useState<string | null>(null);
  // le document change sous le champ (ligne supprimée, import…) : le texte suit, sauf si c'est lui qui vient de produire cette valeur
  // biome-ignore lint/correctness/useExhaustiveDependencies: seule la valeur venue du document déclenche la resynchronisation, pas la frappe
  useEffect(() => {
    const now = parseVars(text);
    if (!now.ok || JSON.stringify(now.value) !== JSON.stringify(value)) {
      setText(varsText(value));
      setError(null);
    }
  }, [value]);
  return (
    <div className="inv-vars">
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <textarea
        id={id}
        rows={1}
        spellCheck={false}
        value={text}
        disabled={disabled}
        placeholder='{"ansible_user": "deploy"}'
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-err` : undefined}
        onChange={(e) => {
          setText(e.target.value);
          const r = parseVars(e.target.value);
          if (r.ok) {
            setError(null);
            onChange(r.value);
          } else setError(r.message);
        }}
      />
      {error && (
        <p id={`${id}-err`} role="alert" className="field-error">
          {error}
        </p>
      )}
    </div>
  );
}

export function Membership({
  label,
  all,
  chosen,
  onToggle,
  disabled,
}: {
  label: string;
  all: string[];
  chosen: string[];
  onToggle: (name: string, on: boolean) => void;
  disabled?: boolean;
}) {
  if (all.length === 0) return <span className="hint">—</span>;
  return (
    <details className="inv-members">
      <summary>
        {chosen.length ? chosen.join(", ") : "aucun"}
        <span className="sr-only"> — {label}</span>
      </summary>
      <fieldset disabled={disabled}>
        <legend className="sr-only">{label}</legend>
        {all.map((n) => (
          <label key={n} className="inline-check">
            <input type="checkbox" checked={chosen.includes(n)} onChange={(e) => onToggle(n, e.target.checked)} /> {n}
          </label>
        ))}
      </fieldset>
    </details>
  );
}

/** Un nom modifiable : il n'est transmis qu'à la sortie du champ (renommer un groupe le renomme partout) et suit le document quand celui-ci change. */
export function NameField({
  id,
  label,
  value,
  onCommit,
  disabled,
  invalid,
}: {
  id: string;
  label: string;
  value: string;
  onCommit: (name: string) => void;
  disabled?: boolean;
  invalid?: boolean;
}) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  return (
    <>
      <label className="sr-only" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        value={text}
        disabled={disabled}
        spellCheck={false}
        aria-invalid={invalid ? true : undefined}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => {
          const to = text.trim();
          if (to !== value) onCommit(to);
          else setText(value);
        }}
      />
    </>
  );
}
