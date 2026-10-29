import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { type ApiError, type JsonSchema } from "../api";
import { defaultsFor } from "../components/SchemaForm";
import { WizardForm } from "../workflow/WizardForm";
import type { FormDef } from "../workflow/types";

interface Def { title: string; description: string; submit_label: string; schema: JsonSchema; form: FormDef | null }

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
  useEffect(() => { if (def.data) setValues(defaultsFor(def.data.schema)); }, [def.data]);
  const send = useMutation<{ ok: boolean; message: string }, ApiError>({
    mutationFn: () => call(kind, `${token}/`, { values, website: trap }),
    onError: (e) => setErrors(e.status === 400 && e.details ? Object.fromEntries(Object.entries(e.details).map(([k, v]) => [k, String(Array.isArray(v) ? v[0] : v)])) : { _: e.message }),
  });
  return (
    <main id="main" className="public-form" style={{ maxWidth: 720, margin: "32px auto", padding: "0 16px" }}>
      {def.isLoading && <p role="status">Chargement…</p>}
      {def.isError && <><h1>Formulaire introuvable</h1><p>Ce formulaire n'existe pas, a déjà reçu une réponse ou n'est plus disponible.</p></>}
      {def.data && (send.data ? (
        <><h1>{def.data.title}</h1><p role="status">{send.data.message}</p></>
      ) : (
        <>
          <h1>{def.data.title}</h1>
          {def.data.description && <p>{def.data.description}</p>}
          <div aria-hidden="true" style={{ position: "absolute", left: -9999 }}><label>Ne pas remplir<input tabIndex={-1} autoComplete="off" value={trap} onChange={(e) => setTrap(e.target.value)} /></label></div>
          <WizardForm schema={def.data.schema} form={def.data.form} values={values} onChange={setValues} errors={errors} submitLabel={def.data.submit_label} busy={send.isPending} onSubmit={() => { setErrors({}); send.mutate(); }} />
          {errors._ && <p role="alert" className="field-error">{errors._}</p>}
        </>
      ))}
    </main>
  );
}
