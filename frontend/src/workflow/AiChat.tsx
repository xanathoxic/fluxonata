import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { api, type ApiError } from "../api";
import { useApp } from "../context";
import { errorText } from "../lib";
import { useDraft } from "./DraftContext";
import { placeNewNodes } from "./graphOps";
import type { Graph, Issue } from "./types";

interface Proposal { graph: Graph; changes: string[]; issues: Issue[]; base: string; status: "pending" | "applied" | "ignored" }
interface Usage { tokens: number; today: number; budget: number }
interface Msg { id: number; role: "user" | "assistant"; text: string; error?: boolean; proposal?: Proposal; usage?: Usage }
interface Reply { reply: string; graph: Graph | null; changes: string[]; issues: Issue[]; usage?: Usage }
interface JobEvent { label: string; detail: string; state: string }
interface JobState { status: "queued" | "running" | "succeeded" | "failed" | "cancelled"; progress: number; events: JobEvent[]; result: Reply | null; error: { code: string; message: string } | null }
const sleep = (ms: number) => new Promise((r) => window.setTimeout(r, ms));

// The conversation survives tab switches (not a page reload): it is a working session, not a record.
const sessions = new Map<string, Msg[]>();
const EXAMPLES = ["Ajoute une décision après la première étape", "Ajoute un script qui calcule la moyenne d'une liste de nombres", "Que fait ce workflow ?"];

/** Chat of the editor: the assistant proposes changes to the draft, the person reviews and applies them (undoable). */
export function AiChat({ onClose, initialMessage }: { onClose: () => void; initialMessage?: string }) {
  const d = useDraft();
  const { workspace } = useApp();
  const key = `${workspace.slug}/${d.slug}`;
  const id = useId();
  const [msgs, setMsgs] = useState<Msg[]>(() => sessions.get(key) ?? []);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState("");
  const jobId = useRef<string | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const log = useRef<HTMLDivElement>(null);
  const next = useRef(msgs.length + 1);
  useEffect(() => { sessions.set(key, msgs); log.current?.scrollTo({ top: log.current.scrollHeight }); }, [key, msgs]);

  const patch = (mid: number, f: (m: Msg) => Msg) => setMsgs((all) => all.map((m) => (m.id === mid ? f(m) : m)));
  const send = async (message: string) => {
    const clean = message.trim();
    if (!clean || busy) return;
    const base = JSON.stringify(d.graph);
    const history = msgs.filter((m) => !m.error).slice(-8).map((m) => ({ role: m.role, text: m.text }));
    setMsgs((all) => [...all, { id: next.current++, role: "user", text: clean }]);
    setText("");
    setBusy(true);
    try {
      // The turn is a job run by the worker (its dry run executes scripts in the sandbox, which the web process never does).
      const job = await api<{ id: string }>(`/workspaces/${workspace.slug}/workflows/${d.slug}/ai/edit-jobs/`, { json: { message: clean, history, graph: d.graph } });
      jobId.current = job.id;
      setPhase("En attente d'un travailleur");
      let state: JobState;
      for (let waited = 0; ; waited += 1) {
        await sleep(1000);
        if (!alive.current) return;
        try { state = await api<JobState>(`/workspaces/${workspace.slug}/ai/draft-jobs/${job.id}/`); } catch (e) { if ((e as ApiError).status === 429) continue; throw e; } // slowed down: try again on the next tick
        const last = state.events[state.events.length - 1];
        setPhase(last ? `${last.label}${last.detail ? ` : ${last.detail}` : ""} (${state.progress} %)` : "");
        if (state.status === "succeeded" || state.status === "failed" || state.status === "cancelled") break;
        if (waited > 900) throw Object.assign(new Error("L'assistant ne répond pas : le travailleur est-il démarré ?"), { status: 504 });
      }
      if (state.status !== "succeeded" || !state.result) throw Object.assign(new Error(state.status === "cancelled" ? "Demande annulée." : state.error?.message ?? "Erreur interne."), { status: 500 });
      const r = state.result;
      setMsgs((all) => [...all, { id: next.current++, role: "assistant", text: r.reply, usage: r.usage, proposal: r.graph ? { graph: r.graph, changes: r.changes, issues: r.issues, base, status: "pending" } : undefined }]);
    } catch (e) {
      const err = e as ApiError;
      setMsgs((all) => [...all, { id: next.current++, role: "assistant", error: true, text: err.status === 403 ? `L'assistant n'est pas disponible : ${err.message}` : `Erreur : ${errorText(err)}` }]);
    } finally { jobId.current = null; setPhase(""); if (alive.current) setBusy(false); }
  };
  const started = useRef(false);
  useEffect(() => { if (initialMessage && !started.current && msgs.length === 0) { started.current = true; void send(initialMessage); } }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const submit = (e: FormEvent) => { e.preventDefault(); void send(text); };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(text); } };
  const apply = (m: Msg) => {
    if (!m.proposal) return;
    d.setGraph(placeNewNodes(m.proposal.graph));
    patch(m.id, (x) => ({ ...x, proposal: { ...(x.proposal as Proposal), status: "applied" } }));
  };
  const stale = (p: Proposal) => p.status === "pending" && p.base !== JSON.stringify(d.graph);

  return (
    <section className="ai-chat" aria-labelledby={`${id}-t`}>
      <header className="ai-chat-head">
        <h2 id={`${id}-t`}>Assistant IA</h2>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Fermer l'assistant IA" title="Fermer">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      </header>
      <p className="hint ai-chat-note">Il propose des modifications du brouillon ; vous les relisez avant de les appliquer (annulables). Il ne publie jamais.</p>
      <div className="ai-chat-log" ref={log} role="log" aria-live="polite" aria-label="Conversation avec l'assistant" tabIndex={0}>
        {msgs.length === 0 && (
          <div className="ai-chat-empty">
            <p>Décrivez ce que vous voulez changer dans ce workflow. Par exemple :</p>
            <ul>{EXAMPLES.map((ex) => <li key={ex}><button type="button" className="linklike" onClick={() => void send(ex)} disabled={busy}>{ex}</button></li>)}</ul>
          </div>
        )}
        {msgs.map((m) => (
          <div key={m.id} className={`ai-msg ai-msg-${m.role}${m.error ? " ai-msg-error" : ""}`}>
            <span className="ai-who">{m.role === "user" ? "Vous" : "Assistant"}</span>
            <p>{m.text}</p>
            {m.usage && <p className="muted"><small>Consommation : {m.usage.tokens.toLocaleString("fr-FR")} jetons pour cette demande ; {m.usage.today.toLocaleString("fr-FR")} sur {m.usage.budget.toLocaleString("fr-FR")} aujourd'hui.</small></p>}
            {m.proposal && <ProposalView m={m} stale={stale(m.proposal)} onApply={() => apply(m)} onIgnore={() => patch(m.id, (x) => ({ ...x, proposal: { ...(x.proposal as Proposal), status: "ignored" } }))} canUndo={d.canUndo} onUndo={d.undo} />}
          </div>
        ))}
        {busy && (
          <p role="status" className="hint">{phase || "L'assistant réfléchit…"} <small>(cela peut prendre une minute avec un modèle local)</small>{" "}
            <button type="button" className="linklike" onClick={() => { if (jobId.current) void api(`/workspaces/${workspace.slug}/ai/draft-jobs/${jobId.current}/cancel/`, { json: {} }); }}>Annuler</button></p>
        )}
      </div>
      <form className="ai-chat-form" onSubmit={submit}>
        <label htmlFor={`${id}-q`} className="sr-only">Votre demande à l'assistant</label>
        <textarea id={`${id}-q`} rows={4} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} disabled={busy} maxLength={6000}
          placeholder="Décrivez la modification souhaitée. Ex. : ajoute un script qui trie la liste, puis une décision sur le résultat. (Entrée pour envoyer, Maj+Entrée pour un retour à la ligne)" />
        <button type="submit" className="primary" disabled={busy || !text.trim()}>Envoyer</button>
      </form>
    </section>
  );
}

function ProposalView({ m, stale, onApply, onIgnore, canUndo, onUndo }: { m: Msg; stale: boolean; onApply: () => void; onIgnore: () => void; canUndo: boolean; onUndo: () => void }) {
  const p = m.proposal as Proposal;
  const errors = p.issues.filter((i) => i.level === "error");
  const reviews = errors.filter((i) => i.code === "ai_review_required").length;
  const others = errors.length - reviews;
  return (
    <div className="ai-proposal">
      <h3>Modifications proposées</h3>
      <ul>{p.changes.map((c) => <li key={c}>{c}</li>)}</ul>
      {(others > 0 || reviews > 0) && (
        <p className="hint">
          {reviews > 0 && `${reviews} script(s) à relire et confirmer avant publication. `}
          {others > 0 && `${others} problème(s) bloquant(s) à corriger après application : ${errors.filter((i) => i.code !== "ai_review_required").slice(0, 2).map((i) => i.message).join(" ")}`}
        </p>
      )}
      {p.status === "pending" && (stale
        ? <p role="alert" className="field-error">Le workflow a changé depuis cette proposition : redemandez-la pour qu'elle en tienne compte.</p>
        : <div className="actions"><button type="button" className="primary" onClick={onApply}>Appliquer au brouillon</button><button type="button" onClick={onIgnore}>Ignorer</button></div>)}
      {p.status === "applied" && <p role="status">✔ Appliqué au brouillon. <button type="button" className="linklike" onClick={onUndo} disabled={!canUndo}>Annuler</button></p>}
      {p.status === "ignored" && <p className="hint">Proposition ignorée.</p>}
    </div>
  );
}
