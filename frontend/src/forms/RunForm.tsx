import { useMemo } from "react";
import { api, type JsonSchema } from "../api";
import { IconLabel } from "./FormIcon";
import { DefinitionForm, type LookupFn } from "./DefinitionForm";
import { errorsToFields, toInputs } from "./inputMap";
import { validateDefinition, visibleState } from "./logic";
import type { FormDefinition } from "./types";

const sleep = (ms: number) => new Promise((r) => window.setTimeout(r, ms));
interface Answer { state: "pending" | "done" | "failed"; options?: { value: string; label?: string }[]; value?: unknown; message?: string }

/** Dynamic lists of a workflow's input form: the server queues the lookup for its worker, we follow it until it is done. */
export function workflowLookup(workspace: string, workflow: string, environment: string): LookupFn {
  const base = `/workspaces/${workspace}/workflows/${workflow}/form-lookup`;
  return async (key, params, kind = "options") => {
    const { id } = await api<{ id: string }>(`${base}/${key}/`, { json: { params, environment, kind } });
    for (let i = 0; i < 40; i++) {
      const r = await api<Answer>(`${base}/${key}/${id}/`);
      if (r.state === "done") return kind === "prefill" ? r.value : r.options ?? [];
      if (r.state === "failed") throw new Error(r.message ?? "La liste n'a pas pu être chargée.");
      await sleep(600);
    }
    throw new Error("La liste met trop de temps à répondre.");
  };
}

interface Props {
  definition: FormDefinition;
  /** the workflow's inputs: with the definition's correspondence (`map`), they decide which field feeds which input */
  schema?: JsonSchema;
  workspace: string;
  workflow: string;
  environment: string;
  values: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  errors: Record<string, string>;
  setErrors: (e: Record<string, string>) => void;
  disabled?: boolean;
  submitLabel: string;
  busy?: boolean;
  /** receives the values of the visible fields only, as the server would keep them */
  onSubmit: (inputs: Record<string, unknown>) => void;
  onCancel?: () => void;
}

/** The designed input form of a workflow, as shown in the catalog and on the run page. */
export function RunForm({ definition, schema, workspace, workflow, environment, values, onChange, errors, setErrors, disabled, submitLabel, busy, onSubmit, onCancel }: Props) {
  const lookup = useMemo(() => workflowLookup(workspace, workflow, environment), [workspace, workflow, environment]);
  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault();
      const problems = validateDefinition(definition, values);
      setErrors(problems);
      if (Object.keys(problems).length === 0) onSubmit(toInputs(definition, schema, definition.map, visibleState(definition, values).kept));
    }}>
      <DefinitionForm definition={definition} values={values} onChange={onChange} errors={errorsToFields(definition, schema, definition.map, errors)} lookup={lookup} disabled={disabled || busy}>
        <button type="submit" className="primary" disabled={disabled || busy} aria-describedby={disabled ? "no-run" : undefined}><IconLabel icon="play">{submitLabel}</IconLabel></button>
        {onCancel && <button type="button" onClick={onCancel}><IconLabel icon="back">Annuler</IconLabel></button>}
      </DefinitionForm>
      {errors._ && <p role="alert" className="field-error">{errors._}</p>}
    </form>
  );
}
