import { useState } from "react";
import { defaultsFor } from "../../components/SchemaForm";
import { useDraft } from "../DraftContext";
import type { FormStep } from "../types";
import { WizardForm } from "../WizardForm";

/** Input form designer: group the workflow inputs into numbered steps, as in a step-by-step launch form. */
export function FormTab() {
  const { graph, setGraph, editable } = useDraft();
  const names = Object.keys(graph.inputs.properties ?? {});
  const steps = graph.form?.steps ?? [];
  const enabled = graph.form != null;
  const used = new Set(steps.flatMap((s) => s.fields));
  const free = names.filter((n) => !used.has(n));
  const write = (next: FormStep[] | null) => setGraph({ ...graph, form: next ? { steps: next } : null });
  const patch = (i: number, p: Partial<FormStep>) => write(steps.map((s, j) => (j === i ? { ...s, ...p } : s)));
  const move = (i: number, d: number) => { const to = i + d; if (to < 0 || to >= steps.length) return; const c = [...steps]; [c[i], c[to]] = [c[to] as FormStep, c[i] as FormStep]; write(c); };
  const [values, setValues] = useState<Record<string, unknown>>(() => defaultsFor(graph.inputs));
  return (
    <section aria-label="Formulaire d'entrée">
      <h2>Formulaire d'entrée</h2>
      <p className="hint">Par défaut, toutes les entrées sont présentées sur une seule page. Un formulaire par étapes guide l'utilisateur, comme un assistant de lancement par étapes.</p>
      <fieldset disabled={!editable} className="bare">
        <div className="field field-inline"><input id="form-on" type="checkbox" checked={enabled} onChange={(e) => write(e.target.checked ? [{ title: "Paramètres", fields: [...names] }] : null)} /><label htmlFor="form-on">Utiliser un formulaire par étapes</label></div>
        {enabled && (
          <>
            {steps.map((s, i) => (
              <fieldset key={i} className="card-lite">
                <legend>Étape {i + 1}</legend>
                <div className="field"><label htmlFor={`fs-t-${i}`}>Titre de l'étape</label><input id={`fs-t-${i}`} value={s.title} onChange={(e) => patch(i, { title: e.target.value })} /></div>
                <div className="field"><label htmlFor={`fs-d-${i}`}>Texte d'aide</label><input id={`fs-d-${i}`} value={s.description ?? ""} onChange={(e) => patch(i, { description: e.target.value })} /></div>
                <fieldset><legend>Champs de cette étape</legend>
                  {s.fields.map((f) => <div key={f} className="edge-row"><span>{graph.inputs.properties?.[f]?.title ?? f}</span><button type="button" onClick={() => patch(i, { fields: s.fields.filter((x) => x !== f) })} aria-label={`Retirer ${f} de l'étape ${i + 1}`}>Retirer</button></div>)}
                  {free.length > 0 && <div className="edge-row"><label htmlFor={`fs-a-${i}`}>Ajouter un champ</label>
                    <select id={`fs-a-${i}`} value="" onChange={(e) => e.target.value && patch(i, { fields: [...s.fields, e.target.value] })}><option value="">Choisir…</option>{free.map((f) => <option key={f} value={f}>{graph.inputs.properties?.[f]?.title ?? f}</option>)}</select></div>}
                </fieldset>
                <div className="actions">
                  <button type="button" onClick={() => move(i, -1)} disabled={i === 0}>Monter</button><button type="button" onClick={() => move(i, 1)} disabled={i === steps.length - 1}>Descendre</button>
                  <button type="button" className="danger" onClick={() => write(steps.filter((_, j) => j !== i))} disabled={steps.length < 2}>Supprimer l'étape</button>
                </div>
              </fieldset>
            ))}
            {free.length > 0 && <p className="hint">Entrées pas encore placées : {free.join(", ")}.</p>}
            <button type="button" onClick={() => write([...steps, { title: `Étape ${steps.length + 1}`, fields: [] }])}>Ajouter une étape</button>
          </>
        )}
      </fieldset>
      <h3>Aperçu</h3>
      {names.length === 0 ? <p className="hint">Ce workflow n'a pas d'entrée.</p> : <WizardForm schema={graph.inputs} form={graph.form} values={values} onChange={setValues} submitLabel="Lancer" preview />}
    </section>
  );
}
