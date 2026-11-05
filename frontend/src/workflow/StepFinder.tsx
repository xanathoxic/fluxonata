import { useState } from "react";
import { Overlay } from "../components/Overlay";
import { NodeIcon } from "./icons";
import { NODE_LABELS, type Graph } from "./types";

/** Quick "go to step" finder (Ctrl+K): type a name, type or identifier, Enter picks the first match. */
export function StepFinder({ graph, onPick, onClose }: { graph: Graph; onPick: (id: string) => void; onClose: () => void }) {
  const [q, setQ] = useState("");
  const f = q.trim().toLowerCase();
  const found = graph.nodes.filter((n) => !f || `${n.name} ${n.id} ${NODE_LABELS[n.type]}`.toLowerCase().includes(f));
  return (
    <Overlay variant="modal" title="Aller à une étape" onClose={onClose}>
      <div className="field">
        <label htmlFor="finder-q" className="sr-only">Nom de l'étape</label>
        <input id="finder-q" type="search" autoFocus placeholder="Nom, type ou identifiant de l'étape" value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && found[0]) onPick(found[0].id); }} />
      </div>
      {found.length === 0 ? <p className="hint">Aucune étape ne correspond.</p> : (
        <ul className="plain finder-list">
          {found.map((n) => (
            <li key={n.id}>
              <button type="button" className="finder-item" onClick={() => onPick(n.id)}>
                <NodeIcon kind={n.type} size={20} /><span className="finder-name">{n.name || n.id}</span><span className="hint">{NODE_LABELS[n.type]}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Overlay>
  );
}
