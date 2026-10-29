import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { api, type ApiError } from "../api";
import { useApp } from "../context";
import { AiNotice } from "./AiBox";
import { AiJobOverlay } from "./AiJobOverlay";
import { GenerationProgress, type DraftJob } from "./GenerationProgress";
import { Overlay } from "./Overlay";
import { HelpTip } from "./HelpTip";

const slugify = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 50);
const TYPES = ["texte", "entier", "nombre", "booléen", "liste", "objet"];

interface OutlineInput { name: string; type: string; purpose: string }
interface Outline {
  title: string; summary: string; inputs: OutlineInput[]; steps: { text: string }[];
  uses: { kind: string; name: string; note: string }[]; assumptions: string[]; questions: { question: string; proposed: string }[];
}
interface OutlineJob { id: string; status: DraftJob["status"]; progress: number; events: DraftJob["events"]; result: { outline: Outline } | null; error: DraftJob["error"] }

/** What the person edits: plain text areas (one item per line), turned back into the structured plan on validation. */
interface Edit { title: string; summary: string; inputs: string; steps: string; assumptions: string; answers: string[] }

const toEdit = (o: Outline): Edit => ({
  title: o.title, summary: o.summary,
  inputs: o.inputs.map((i) => `${i.name} (${i.type})${i.purpose ? ` : ${i.purpose}` : ""}`).join("\n"),
  steps: o.steps.map((s) => s.text).join("\n"),
  assumptions: o.assumptions.join("\n"),
  answers: o.questions.map((q) => q.proposed),
});
const lines = (t: string) => t.split("\n").map((l) => l.trim()).filter(Boolean);
function fromEdit(e: Edit, base: Outline): Outline & { answers: { question: string; answer: string }[] } {
  const inputs = lines(e.inputs).map((l) => {
    const m = /^([A-Za-z0-9_]+)\s*(?:\(([^)]*)\))?\s*(?::\s*(.*))?$/.exec(l);
    const type = (m?.[2] ?? "").trim().toLowerCase();
    return { name: (m?.[1] ?? l).toLowerCase(), type: TYPES.includes(type) ? type : "texte", purpose: (m?.[3] ?? "").trim() };
  });
  return {
    ...base, title: e.title.trim() || base.title, summary: e.summary.trim(), inputs,
    steps: lines(e.steps).map((text) => ({ text })), assumptions: lines(e.assumptions),
    answers: base.questions.map((q, i) => ({ question: q.question, answer: (e.answers[i] ?? "").trim() })),
  };
}

function useJob<T extends { status: string }>(id: string | null, kind: "draft" | "outline") {
  const { workspace } = useApp();
  return useQuery({
    queryKey: [`ai-${kind}-job`, id], enabled: !!id, staleTime: 0,
    queryFn: () => api<T>(`/workspaces/${workspace.slug}/ai/draft-jobs/${id}/`),
    refetchInterval: (q) => (q.state.data && (q.state.data.status === "succeeded" || q.state.data.status === "failed") ? false : 1000),
  });
}

/** Generate a workflow with the assistant: describe it, validate (or edit, or refuse) the plan it envisages, then follow the generation. */
export function AiDraftFlow({ mode, onClose, onOpenDraft }: { mode: string; onClose: () => void; onOpenDraft: (slug: string) => void }) {
  const { workspace } = useApp();
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [outlineId, setOutlineId] = useState<string | null>(null);
  const [edit, setEdit] = useState<Edit | null>(null);
  const [feedback, setFeedback] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);

  const startOutline = useMutation<{ id: string }, ApiError, { previous?: unknown; feedback?: string }>({
    mutationFn: (extra) => api(`/workspaces/${workspace.slug}/ai/outline-jobs/`, { json: { request: text, ...extra } }),
    onSuccess: (r) => { setOutlineId(r.id); setEdit(null); },
  });
  const startDraft = useMutation<{ id: string }, ApiError, { outline?: unknown }>({
    mutationFn: ({ outline }) => api(`/workspaces/${workspace.slug}/ai/draft-jobs/`, { json: { request: text, name: name || (outline as Outline | undefined)?.title || "Brouillon IA", slug: slugify(name || (outline as Outline | undefined)?.title || `brouillon-ia-${Date.now() % 100000}`), ...(outline ? { outline } : {}) } }),
    onSuccess: (r) => setJobId(r.id),
  });
  const outlineJob = useJob<OutlineJob>(outlineId, "outline").data ?? null;
  const draftJob = useJob<DraftJob>(jobId, "draft").data ?? null;
  const outline = outlineJob?.status === "succeeded" ? outlineJob.result?.outline ?? null : null;
  const draftFailed = draftJob?.status === "failed" ? draftJob.error : draftJob?.status === "cancelled" ? draftJob.error : null;
  const outlineFailed = outlineJob?.status === "failed" ? outlineJob.error : null;
  const planning = !!outlineId && !outline && !outlineFailed;

  // a new plan (first or revised) replaces what is being edited
  useEffect(() => { if (outline && outlineId) setEdit(toEdit(outline)); }, [outline, outlineId]);

  const close = () => { onClose(); };
  const backToForm = () => { setOutlineId(null); setEdit(null); setJobId(null); startDraft.reset(); startOutline.reset(); };

  // ---- 3. the draft is being generated (or is ready): the same view as the tray's, with progress, cancel and report
  if (jobId && !draftFailed) return <AiJobOverlay jobId={jobId} onClose={close} onOpenDraft={onOpenDraft} />;
  // ---- 2. the plan the assistant envisages
  if (outlineId && !draftFailed) {
    const reviewing = outline && edit;
    return (
      <Overlay wide variant="modal" title={reviewing ? "Plan envisagé : validez, modifiez ou refusez" : "L'assistant prépare un plan"} onClose={close}
        description="Rien n'est généré tant que vous n'avez pas validé ce plan. Il est écrit en mots simples : corrigez-le comme vous voulez."
        footer={reviewing ? (
          <>
            <button type="button" onClick={backToForm}>Refuser ce plan</button>
            <button type="button" className="primary" disabled={startDraft.isPending || !edit.steps.trim()} onClick={() => startDraft.mutate({ outline: fromEdit(edit, outline) })}>Valider et générer</button>
          </>
        ) : <button type="button" onClick={backToForm}>Annuler</button>}>
        {planning && outlineJob && <GenerationProgress job={{ ...outlineJob, result: null } as unknown as DraftJob} />}
        {planning && !outlineJob && <p role="status">Démarrage…</p>}
        {outlineFailed && <p role="alert" className="field-error">Le plan n'a pas pu être préparé : {outlineFailed.message} Vous pouvez reformuler votre demande ou générer directement.</p>}
        {reviewing && (
          <form onSubmit={(e: FormEvent) => e.preventDefault()} className="outline-form" aria-label="Plan envisagé">
            <div className="field"><label htmlFor="ol-title">Nom du workflow</label><input id="ol-title" value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })} /></div>
            <div className="field"><label htmlFor="ol-sum">En une phrase</label><textarea id="ol-sum" rows={2} value={edit.summary} onChange={(e) => setEdit({ ...edit, summary: e.target.value })} /></div>
            <div className="field"><label htmlFor="ol-in">Informations demandées au lancement (une par ligne : nom (type) : à quoi ça sert)</label>
              <textarea id="ol-in" rows={Math.min(8, Math.max(3, edit.inputs.split("\n").length))} className="mono" value={edit.inputs} onChange={(e) => setEdit({ ...edit, inputs: e.target.value })} /></div>
            <div className="field"><label htmlFor="ol-steps">Ce que fera le workflow, dans l'ordre (une étape par ligne)</label>
              <textarea id="ol-steps" rows={Math.min(14, Math.max(5, edit.steps.split("\n").length + 1))} value={edit.steps} onChange={(e) => setEdit({ ...edit, steps: e.target.value })} aria-describedby="ol-steps-help" />
              <p id="ol-steps-help" className="hint">Les textes entre « » (titre d'une approbation, message d'une exception) seront repris tels quels.</p></div>
            {outline.uses.length > 0 && (
              <div><h3>Déjà disponible dans votre espace et réutilisé</h3>
                <ul>{outline.uses.map((u) => <li key={`${u.kind}-${u.name}`}><strong>{u.name}</strong> ({u.kind}){u.note ? ` : ${u.note}` : ""}</li>)}</ul></div>
            )}
            <div className="field"><label htmlFor="ol-ass">Ce que l'assistant a supposé (une hypothèse par ligne)</label>
              <textarea id="ol-ass" rows={Math.min(8, Math.max(2, edit.assumptions.split("\n").length))} value={edit.assumptions} onChange={(e) => setEdit({ ...edit, assumptions: e.target.value })} /></div>
            {outline.questions.length > 0 && (
              <fieldset><legend>Questions de l'assistant (une réponse est proposée, changez-la si besoin)</legend>
                {outline.questions.map((q, i) => (
                  <div className="field" key={q.question}>
                    <label htmlFor={`ol-q${i}`}>{q.question}</label>
                    <input id={`ol-q${i}`} value={edit.answers[i] ?? ""} onChange={(e) => setEdit({ ...edit, answers: edit.answers.map((a, k) => (k === i ? e.target.value : a)) })} />
                  </div>
                ))}
              </fieldset>
            )}
            <div className="field"><label htmlFor="ol-fb">Une autre idée ? Dites-le avec vos mots (« ajoute une notification à la fin », « supprime l'attente »…)</label>
              <textarea id="ol-fb" rows={2} value={feedback} onChange={(e) => setFeedback(e.target.value)} /></div>
            <button type="button" disabled={!feedback.trim() || startOutline.isPending}
              onClick={() => { startOutline.mutate({ previous: fromEdit(edit, outline), feedback }, { onSuccess: () => setFeedback("") }); }}>Réviser le plan avec cette consigne</button>
            {startDraft.error && <p role="alert" className="field-error">Erreur : {startDraft.error.message}</p>}
          </form>
        )}
      </Overlay>
    );
  }
  // ---- 1. describe what is wanted
  return (
    <Overlay wide variant="modal" title="Générer un brouillon avec l'assistant IA" onClose={close}
      description="Décrivez le workflow souhaité avec vos mots : l'assistant propose d'abord un plan que vous validez, puis un brouillon que vous relisez, corrigez et publiez vous-même."
      footer={<>
        <button type="button" onClick={close}>Annuler</button>
        <button type="button" disabled={!text.trim() || startDraft.isPending || startOutline.isPending} onClick={() => startDraft.mutate({})}>Générer sans plan</button>
        <button type="submit" form="ai-draft-form" className="primary" disabled={!text.trim() || startDraft.isPending || startOutline.isPending}>{startOutline.isPending ? "Démarrage…" : "Proposer un plan"}</button>
      </>}>
      <form id="ai-draft-form" onSubmit={(e: FormEvent) => { e.preventDefault(); if (text.trim()) startOutline.mutate({}); }}>
        <div className="field"><label htmlFor="ai-req">Décrivez en français ce que le workflow doit faire</label><textarea id="ai-req" rows={5} value={text} onChange={(e) => setText(e.target.value)} maxLength={6000} /></div>
        <div className="field"><label htmlFor="wf-name-ai">Nom du brouillon (facultatif : sinon, celui du plan)</label><input id="wf-name-ai" placeholder="ex. Redémarrer des VM" value={name} onChange={(e) => setName(e.target.value)} /></div>
        <AiNotice mode={mode} />
        <p className="help-row"><HelpTip caption="À savoir">Le résultat est toujours un brouillon : vous le relisez, le corrigez et le publiez vous-même. « Générer sans plan » est réservé aux demandes déjà très précises.</HelpTip></p>
        {draftFailed && <p role="alert" className="field-error">La génération a échoué : {draftFailed.message}</p>}
        {(startOutline.error || startDraft.error) && <p role="alert" className="field-error">Erreur : {(startOutline.error ?? startDraft.error)?.message}</p>}
      </form>
    </Overlay>
  );
}
