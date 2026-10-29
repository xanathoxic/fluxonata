import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useApp } from "../context";
import { api, type ApiError } from "../api";
import { DraftReport, GenerationProgress, type DraftJob } from "./GenerationProgress";
import { Overlay } from "./Overlay";
import { useQuery } from "@tanstack/react-query";

/** One generation, wherever the person opens it from (the generation window, the tray): progress and log while it runs,
 *  a way to CANCEL it, then the report and a way into the editor; or the reason it stopped. */
export function AiJobOverlay({ jobId, onClose, onOpenDraft }: { jobId: string; onClose: () => void; onOpenDraft: (slug: string) => void }) {
  const { workspace } = useApp();
  const qc = useQueryClient();
  const { data: job } = useQuery({
    queryKey: ["ai-draft-job", jobId], staleTime: 0,
    queryFn: () => api<DraftJob>(`/workspaces/${workspace.slug}/ai/draft-jobs/${jobId}/`),
    refetchInterval: (q) => (q.state.data && ["succeeded", "failed", "cancelled"].includes(q.state.data.status) ? false : 1000),
  });
  const cancel = useMutation<unknown, ApiError>({
    mutationFn: () => api(`/workspaces/${workspace.slug}/ai/draft-jobs/${jobId}/cancel/`, { json: {} }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["ai-draft-job", jobId] }); void qc.invalidateQueries({ queryKey: ["ai-draft-jobs"] }); },
  });
  const done = job?.status === "succeeded" ? job.result : null;
  useEffect(() => { if (done) void qc.invalidateQueries({ queryKey: ["workflows"] }); }, [done, qc]);
  if (!job) return <Overlay wide variant="modal" title="Génération du brouillon" onClose={onClose}><p role="status">Chargement…</p></Overlay>;

  if (done) {
    return (
      <Overlay wide variant="modal" title="Brouillon généré" onClose={onClose}
        description="Le brouillon est enregistré, pas publié. Relisez-le dans l'éditeur avant toute publication."
        footer={<button type="button" className="primary" onClick={() => onOpenDraft(done.workflow)}>Ouvrir dans l'éditeur</button>}>
        <DraftReport result={done} />
      </Overlay>
    );
  }
  if (job.status === "failed" || job.status === "cancelled") {
    return (
      <Overlay wide variant="modal" title={job.status === "cancelled" ? "Génération annulée" : "La génération a échoué"} onClose={onClose}
        footer={<button type="button" className="primary" onClick={onClose}>Fermer</button>}>
        <p role={job.status === "cancelled" ? "status" : "alert"} className={job.status === "failed" ? "field-error" : undefined}>{job.error?.message ?? "Génération interrompue."}</p>
        <GenerationProgress job={job} />
      </Overlay>
    );
  }
  return (
    <Overlay wide variant="modal" title="Génération du brouillon en cours" onClose={onClose}
      description="L'assistant rédige le plan, construit le schéma, le contrôle puis l'exécute à blanc. Vous pouvez fermer cette fenêtre : la génération continue, et reste visible dans l'en-tête de l'écran (bouton « Générations »)."
      footer={<>
        <button type="button" onClick={() => cancel.mutate()} disabled={cancel.isPending}>{cancel.isPending ? "Annulation…" : "Annuler la génération"}</button>
        <button type="button" className="primary" onClick={onClose}>Fermer (la génération continue)</button>
      </>}>
      {cancel.error && <p role="alert" className="field-error">Annulation impossible : {cancel.error.message}</p>}
      <GenerationProgress job={job} />
    </Overlay>
  );
}
