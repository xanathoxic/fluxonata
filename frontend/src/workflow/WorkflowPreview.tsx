import { useEffect, useRef, useState } from "react";
import type { WorkflowDetail } from "../api";
import { Overlay } from "../components/Overlay";
import { useWsQuery } from "../lib";
import { CanvasView } from "./CanvasView";
import { ReadOnlyDraftProvider, useDraft } from "./DraftContext";
import { NodePanel } from "./NodePanel";
import { ScriptEditorDialog } from "./ScriptEditorDialog";

const noop = () => undefined;

/** The called workflow as it is drawn in its own editor, nothing editable: schema, properties of each step, scripts. A called-workflow step in this
 *  view opens nothing (one overlay level only). */
function Viewer() {
  const { graph, actions, workflows, slug } = useDraft();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [scriptId, setScriptId] = useState<string | null>(null);
  const scriptBox = useRef<HTMLDivElement>(null);
  useEffect(() => { if (scriptId) scriptBox.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" }); }, [scriptId]);
  const selected = graph.nodes.find((n) => n.id === selectedId) ?? null;
  const script = graph.nodes.find((n) => n.id === scriptId && n.type === "script") ?? null;
  return (
    <div className={`wfp${selected ? " wfp-with-panel" : ""}`}>
      <div className="wfp-top">
      <div className="wfp-canvas">
        <CanvasView graph={graph} actions={actions} issues={[]} selectedId={selectedId} onSelect={setSelectedId} readOnly
          onOpen={(id) => { if (graph.nodes.find((n) => n.id === id)?.type === "script") setScriptId(id); }} />
      </div>
      {selected && (
        <aside className="schema-panel wfp-panel" aria-label="Propriétés de l'étape (lecture seule)">
          <NodePanel graph={graph} node={selected} actions={actions} workflows={workflows} issues={[]} editable={false} preview
            onUpdate={noop} onConnect={noop} onDisconnect={noop} onRelabel={noop} onDuplicate={noop} onDelete={noop}
            onOpenScript={() => setScriptId(selected.id)} onClose={() => setSelectedId(null)} />
        </aside>
      )}
      </div>
      {script && (
        <div ref={scriptBox} className="wfp-script">
          <ScriptEditorDialog inline node={script} graph={graph} actions={actions} slug={slug} editable={false} onUpdate={noop} onClose={() => setScriptId(null)} />
        </div>
      )}
    </div>
  );
}

/** Overlay opened by a double click on a called-workflow step: the called workflow's current draft, read only. */
export function WorkflowPreview({ slug, onClose }: { slug: string; onClose: () => void }) {
  const parent = useDraft();
  const q = useWsQuery<WorkflowDetail>(`workflow-${slug}`, `/workflows/${slug}/`);
  return (
    <Overlay title={q.data ? `${q.data.name} (lecture seule)` : "Workflow appelé"} variant="modal" wide className="overlay-preview" onClose={onClose}
      description="Brouillon actuel du workflow appelé. Pour le modifier, ouvrez-le dans son propre éditeur.">
      {q.isError ? <p role="alert" className="field-error">Ce workflow est introuvable ou vous n'avez pas le droit de le voir.</p>
        : !q.data ? <p role="status">Chargement…</p>
        : (
          <ReadOnlyDraftProvider slug={slug} wf={q.data} actions={parent.actions} workflows={parent.workflows}>
            <Viewer />
          </ReadOnlyDraftProvider>
        )}
    </Overlay>
  );
}
