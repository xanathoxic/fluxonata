import { useState } from "react";
import type { JsonSchema } from "../api";
import { SchemaForm, validateValues } from "../components/SchemaForm";
import type { FormDef } from "./types";

interface Props {
  schema: JsonSchema;
  form?: FormDef | null;
  values: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  errors?: Record<string, string>;
  disabled?: boolean;
  submitLabel: string;
  busy?: boolean;
  onSubmit?: () => void;
  onCancel?: () => void;
  preview?: boolean;
}

/** "Start workflow" wizard: numbered steps on the left (✔ when completed), the step's fields on the right, Back / Next / Submit. */
export function WizardForm({ schema, form, values, onChange, errors = {}, disabled, submitLabel, busy, onSubmit, onCancel, preview }: Props) {
  const all = Object.keys(schema.properties ?? {});
  const steps = form?.steps?.length ? form.steps : [{ title: "Entrées", fields: all }];
  const [idx, setIdx] = useState(0);
  const [localErrors, setLocalErrors] = useState<Record<string, string>>({});
  const [done, setDone] = useState<Set<number>>(new Set());
  const step = steps[idx] ?? steps[0];
  const sub = (fields: string[]): JsonSchema => ({
    ...schema, required: (schema.required ?? []).filter((r) => fields.includes(r)),
    properties: Object.fromEntries(fields.filter((f) => schema.properties?.[f]).map((f) => [f, schema.properties?.[f] as JsonSchema])),
  });
  const stepErrors = (i: number) => validateValues(sub(steps[i]?.fields ?? []), values);
  const next = () => {
    const e = stepErrors(idx);
    setLocalErrors(e);
    if (Object.keys(e).length) return;
    setDone(new Set(done).add(idx));
    setIdx(idx + 1);
  };
  const submit = () => {
    const all = Object.assign({}, ...steps.map((_, i) => stepErrors(i)));
    setLocalErrors(all);
    const firstBad = steps.findIndex((_, i) => Object.keys(stepErrors(i)).length);
    if (firstBad >= 0) { setIdx(firstBad); return; }
    onSubmit?.();
  };
  const merged = { ...localErrors, ...errors };
  const last = idx === steps.length - 1;
  return (
    <form className="wizard" noValidate onSubmit={(e) => { e.preventDefault(); if (last) submit(); else next(); }}>
      {steps.length > 1 && (
        <nav className="wizard-steps" aria-label="Étapes du formulaire">
          <ol>
            {steps.map((s, i) => (
              <li key={i} aria-current={i === idx ? "step" : undefined} className={i === idx ? "current" : ""}>
                <span aria-hidden="true">{done.has(i) ? "✔" : i + 1}</span> <span>{s.title}{done.has(i) ? <span className="sr-only"> (terminée)</span> : null}</span>
              </li>
            ))}
          </ol>
        </nav>
      )}
      <div className="wizard-body">
        <h2>{steps.length > 1 ? `${idx + 1}. ${step?.title}` : "Entrées"}</h2>
        {step?.description && <p className="hint">{step.description}</p>}
        <SchemaForm schema={sub(step?.fields ?? [])} values={values} onChange={onChange} errors={merged} disabled={disabled} />
        {merged._ && <p role="alert" className="field-error">Erreur : {merged._}</p>}
        <div className="actions">
          {onCancel && <button type="button" onClick={onCancel}>Annuler</button>}
          {idx > 0 && <button type="button" onClick={() => setIdx(idx - 1)}>Précédent</button>}
          {!last && <button type="submit">Suivant</button>}
          {last && <button type="submit" className="primary" disabled={disabled || busy || preview}>{busy ? "Lancement…" : submitLabel}</button>}
        </div>
        {preview && <p className="hint">Aperçu : le lancement est désactivé.</p>}
      </div>
    </form>
  );
}
