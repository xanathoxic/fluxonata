import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../context";
import { AiJobOverlay } from "./AiJobOverlay";
import { useAiMode } from "./AiBox";

interface TrayJob { id: string; status: "queued" | "running" | "succeeded" | "failed" | "cancelled"; progress: number; name: string; phase: string; detail: string; error: string | null; workflow: string | null }
const KEY = "orch.aijobs.dismissed";
const read = (): string[] => { try { return JSON.parse(localStorage.getItem(KEY) ?? "[]") as string[]; } catch { return []; } };

/** Where a generation can be followed (and cancelled) after its window was closed, on every page: bottom right. */
export function AiJobsTray() {
  const { workspace, can } = useApp();
  const ai = useAiMode();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<string[]>(read);
  const enabled = ai.enabled && can("edit");
  const { data } = useQuery({
    queryKey: ["ai-draft-jobs", workspace.slug], enabled,
    queryFn: () => api<{ results: TrayJob[] }>(`/workspaces/${workspace.slug}/ai/draft-jobs/`),
    refetchInterval: (q) => (q.state.data?.results.some((j) => j.status === "queued" || j.status === "running") ? 2000 : 15000),
  });
  const cancel = useMutation<unknown, Error, string>({
    mutationFn: (id) => api(`/workspaces/${workspace.slug}/ai/draft-jobs/${id}/cancel/`, { json: {} }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["ai-draft-jobs"] }); },
  });
  const dismiss = (id: string) => { const next = [...dismissed, id].slice(-50); setDismissed(next); try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* storage may be unavailable */ } };
  const jobs = (data?.results ?? []).filter((j) => (j.status === "queued" || j.status === "running") || (j.status !== "cancelled" && !dismissed.includes(j.id)));
  const slot = typeof document !== "undefined" ? document.getElementById("jobs-slot") : null;
  const running = jobs.filter((j) => j.status === "queued" || j.status === "running").length;
  const [panel, setPanel] = useState(false);
  return (
    <>
      {jobs.length > 0 && slot && createPortal(
        <div className="jobs-menu">
          <button type="button" className="jobs-pill" aria-expanded={panel} aria-controls="jobs-panel" onClick={() => setPanel(!panel)}>
            <span aria-hidden="true">{running ? "⏳" : "✔"}</span> Générations ({jobs.length})
          </button>
          {panel && <div id="jobs-panel" className="jobs-panel">{trayCards()}</div>}
        </div>,
        slot,
      )}
      {open && <AiJobOverlay jobId={open} onClose={() => setOpen(null)} onOpenDraft={(slug) => { setOpen(null); navigate(`/workflows/${slug}/schema`); }} />}
    </>
  );
  function trayCards() {
    return (
        <section aria-label="Suivi des générations de workflows">
          {jobs.map((j) => {
            const active = j.status === "queued" || j.status === "running";
            return (
              <article key={j.id} className="jobs-card" aria-label={`Génération : ${j.name}`}>
                <h3>{j.name}</h3>
                {active && (
                  <>
                    <p role="status" className="hint">{j.status === "queued" ? "En attente d'un travailleur…" : `${j.phase}${j.detail ? ` — ${j.detail}` : ""}`}</p>
                    <progress max={100} value={j.progress} aria-label={`Avancement de la génération ${j.name}`} />
                    <div className="actions">
                      <button type="button" onClick={() => setOpen(j.id)}>Suivre</button>
                      <button type="button" onClick={() => cancel.mutate(j.id)} disabled={cancel.isPending}>Annuler la génération</button>
                    </div>
                  </>
                )}
                {j.status === "succeeded" && (
                  <>
                    <p role="status">✔ Brouillon prêt.</p>
                    <div className="actions">
                      <button type="button" className="primary" onClick={() => setOpen(j.id)}>Voir le rapport</button>
                      <button type="button" onClick={() => dismiss(j.id)} aria-label={`Fermer la notification de ${j.name}`}>Fermer</button>
                    </div>
                  </>
                )}
                {j.status === "failed" && (
                  <>
                    <p role="alert" className="field-error">✖ Échec : {j.error ?? "erreur"}</p>
                    <div className="actions"><button type="button" onClick={() => dismiss(j.id)} aria-label={`Fermer la notification de ${j.name}`}>Fermer</button></div>
                  </>
                )}
              </article>
            );
          })}
        </section>
    );
  }
}
