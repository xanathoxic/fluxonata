import { useMutation, useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { type ApiError, type JsonSchema } from "../api";
import { defaultsFor } from "../components/SchemaForm";
import { WizardForm } from "../workflow/WizardForm";
import type { FormDef } from "../workflow/types";
import { DefinitionForm, type LookupFn } from "../forms/DefinitionForm";
import { validateDefinition, visibleState } from "../forms/logic";
import type { FormDefinition } from "../forms/types";
import { WIDTHS } from "../forms/style";

/** The page is at least as wide as the form it carries (a « full » form uses the room there is, up to a comfortable limit). */
const pageWidth = (w: keyof typeof WIDTHS | undefined) => (w === "full" ? 1400 : Math.max(720, (w && WIDTHS[w]) || 720));

interface Def { title: string; description: string; submit_label: string; schema?: JsonSchema; form?: FormDef | null; definition?: FormDefinition }
interface LookupAnswer { state: "pending" | "done" | "failed"; options?: { value: string; label?: string }[]; value?: unknown; message?: string }
const sleep = (ms: number) => new Promise((r) => window.setTimeout(r, ms));

async function call<T>(kind: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api/v1/${kind}/${path}`, { method: body ? "POST" : "GET", headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
  const data = (await r.json().catch(() => ({}))) as Record<string, unknown>;
  if (!r.ok) throw { status: r.status, message: (data.detail as string) ?? "Envoi impossible.", details: data } as unknown as ApiError;
  return data as T;
}

/** The page behind a public form address: no sign-in, nothing about the platform but the form itself. */
export function PublicFormPage({ kind = "forms" }: { kind?: "forms" | "resume-forms" }) {
  const { token = "" } = useParams();
  const def = useQuery({ queryKey: ["public-form", token], queryFn: () => call<Def>(kind, `${token}/`), retry: false });
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [trap, setTrap] = useState("");
  useEffect(() => { if (def.data?.schema) setValues(defaultsFor(def.data.schema)); }, [def.data]);
  // dynamic lists: the server queues the lookup for its worker, the page follows it until it is done
  const lookup = useCallback<LookupFn>(async (key, params, kind = "options") => {
    const { id } = await call<{ id: string }>("forms", `${token}/lookup/${key}/`, { params, kind });
    for (let i = 0; i < 40; i++) {
      const r = await call<LookupAnswer>("forms", `${token}/lookup/${key}/${id}/`);
      if (r.state === "done") return kind === "prefill" ? r.value : r.options ?? [];
      if (r.state === "failed") throw new Error(r.message ?? "La liste n'a pas pu être chargée.");
      await sleep(600);
    }
    throw new Error("La liste met trop de temps à répondre.");
  }, [token]);
  const submitDefinition = (d: FormDefinition) => {
    const problems = validateDefinition(d, values);
    setErrors(problems);
    if (Object.keys(problems).length === 0) send.mutate(visibleState(d, values).kept);
  };
  const send = useMutation<{ ok: boolean; message: string }, ApiError, Record<string, unknown> | void>({
    mutationFn: (sent) => call(kind, `${token}/`, { values: sent ?? values, website: trap }),
    onError: (e) => setErrors(e.status === 400 && e.details ? Object.fromEntries(Object.entries(e.details).map(([k, v]) => [k, String(Array.isArray(v) ? v[0] : v)])) : { _: e.message }),
  });
  return (
    <main id="main" className="public-form" style={{ maxWidth: pageWidth(def.data?.definition?.style?.width), margin: "32px auto", padding: "0 16px" }}>
      {def.isLoading && <p role="status">Chargement…</p>}
      {def.isError && <><h1>Formulaire introuvable</h1><p>Ce formulaire n'existe pas, a déjà reçu une réponse ou n'est plus disponible.</p></>}
      {def.data && (send.data ? (
        <><h1>{def.data.title}</h1><p role="status">{send.data.message}</p></>
      ) : (
        <>
          <h1>{def.data.title}</h1>
          {def.data.description && <p>{def.data.description}</p>}
          <div aria-hidden="true" style={{ position: "absolute", left: -9999 }}><label>Ne pas remplir<input tabIndex={-1} autoComplete="off" value={trap} onChange={(e) => setTrap(e.target.value)} /></label></div>
          {def.data.definition ? (
            <form noValidate onSubmit={(e) => { e.preventDefault(); submitDefinition(def.data.definition as FormDefinition); }}>
              <DefinitionForm definition={def.data.definition} values={values} onChange={setValues} errors={errors} lookup={lookup} disabled={send.isPending}>
                <button type="submit" className="primary" disabled={send.isPending}>{def.data.submit_label}</button>
              </DefinitionForm>
            </form>
          ) : def.data.schema && (
            <WizardForm schema={def.data.schema} form={def.data.form ?? null} values={values} onChange={setValues} errors={errors} submitLabel={def.data.submit_label} busy={send.isPending} onSubmit={() => { setErrors({}); send.mutate(); }} />
          )}
          {errors._ && <p role="alert" className="field-error">{errors._}</p>}
        </>
      ))}
    </main>
  );
}
