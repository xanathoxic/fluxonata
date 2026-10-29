import { useState } from "react";
import { useApp } from "../context";
import { formatDate } from "../i18n";
import { errorText, useWsMutation, useWsQuery } from "../lib";
import { useDraft } from "./DraftContext";
import type { GraphNode } from "./types";

interface Comment { id: number; node_id: string; author: string | null; text: string; resolved: boolean; created_at: string }

/** Remarks left on a step (review, questions). Readers can comment; the author or an editor can resolve or remove. */
export function CommentsTab({ node }: { node: GraphNode }) {
  const d = useDraft();
  const { can } = useApp();
  const key = `wf-comments-${d.slug}`;
  const q = useWsQuery<{ results: Comment[] }>(key, `/workflows/${d.slug}/comments/`);
  const add = useWsMutation<{ node_id: string; text: string }>("POST", `/workflows/${d.slug}/comments/`, [key]);
  const patch = useWsMutation<{ id: number; resolved: boolean }>("PATCH", (i) => `/workflows/${d.slug}/comments/${i.id}/`, [key]);
  const del = useWsMutation<{ id: number }>("DELETE", (i) => `/workflows/${d.slug}/comments/${i.id}/`, [key]);
  const [text, setText] = useState("");
  const mine = (q.data?.results ?? []).filter((c) => c.node_id === node.id);
  return (
    <div>
      {mine.length === 0 && <p className="hint">Aucune remarque sur cette étape.</p>}
      <ul className="plain">{mine.map((c) => (
        <li key={c.id} className="card-lite" style={c.resolved ? { opacity: 0.6 } : undefined}>
          <p><strong>{c.author ?? "—"}</strong> <span className="hint">{formatDate(c.created_at)}{c.resolved ? " · résolue" : ""}</span></p>
          <p style={{ whiteSpace: "pre-wrap" }}>{c.text}</p>
          <button type="button" onClick={() => patch.mutate({ id: c.id, resolved: !c.resolved })}>{c.resolved ? "Rouvrir" : "Marquer comme résolue"}</button>{" "}
          <button type="button" onClick={() => del.mutate({ id: c.id })} aria-label={`Supprimer la remarque de ${c.author ?? "—"}`}>Supprimer</button>
        </li>))}</ul>
      <div className="field"><label htmlFor="cm-text">Nouvelle remarque</label><textarea id="cm-text" rows={3} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} /></div>
      <button type="button" className="primary" disabled={!text.trim() || add.isPending} onClick={() => add.mutate({ node_id: node.id, text }, { onSuccess: () => setText("") })}>Ajouter la remarque</button>
      {(add.error || patch.error || del.error) && <p role="alert" className="field-error">Erreur : {errorText((add.error ?? patch.error ?? del.error) as never)}</p>}
      {!can("edit") && <p className="hint">Vous pouvez commenter ; seuls les éditeurs modifient le workflow.</p>}
    </div>
  );
}
