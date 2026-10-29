import type { Action } from "../api";
import { summarize, topoOrder } from "./graphOps";
import { edgeText, NODE_LABELS, type Graph, type Issue } from "./types";

interface Props {
  graph: Graph;
  actions: Action[];
  issues: Issue[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

/** Keyboard- and screen-reader-friendly equivalent of the canvas (no drag and drop needed). */
export function ListView({ graph, actions, issues, selectedId, onSelect }: Props) {
  const order = topoOrder(graph) ?? graph.nodes.map((n) => n.id);
  if (graph.nodes.length === 0) return <p className="empty">Aucune étape. Ajoutez une étape depuis le catalogue à gauche.</p>;
  return (
    <ol className="step-list" aria-label="Étapes du workflow">
      {order.map((id, i) => {
        const n = graph.nodes.find((x) => x.id === id);
        if (!n) return null;
        const errs = issues.filter((x) => x.node === id && x.level === "error").length;
        const after = graph.edges.filter((e) => e.to === id).map((e) => graph.nodes.find((x) => x.id === e.from)?.name ?? e.from);
        const next = graph.edges.filter((e) => e.from === id);
        return (
          <li key={id}>
            <button type="button" className={`step-row${id === selectedId ? " selected" : ""}`} aria-current={id === selectedId ? "true" : undefined} onClick={() => onSelect(id)}>
              <span className="step-index">{i + 1}.</span>
              <span className="step-main">
                <strong>{n.name}</strong> <em>({NODE_LABELS[n.type]})</em>
                <span className="step-summary">{summarize(n, actions)}</span>
                {after.length > 0 && <span className="step-summary">Après : {after.join(", ")}</span>}
                {next.length > 0 && (
                  <span className="step-summary">
                    Ensuite : {next.map((e) => `${graph.nodes.find((x) => x.id === e.to)?.name ?? e.to}${e.label ? ` (${edgeText(e.label)})` : ""}`).join(", ")}
                  </span>
                )}
              </span>
              <span className="step-state">{errs ? `✖ ${errs} problème(s)` : "✔ Valide"}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
