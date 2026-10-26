import { useEffect, useState } from "react";
import { useAiMode } from "../../components/AiBox";
import { AiChat } from "../AiChat";
import { CanvasView } from "../CanvasView";
import { useDraft } from "../DraftContext";
import { addNode, autoLayout, connect, defaultNode, disconnect, duplicateNode, moveNode, relabel, removeNode, updateNode } from "../graphOps";
import { ListView } from "../ListView";
import { NodePanel } from "../NodePanel";
import { PaneResizer } from "../PaneResizer";
import { DEFAULT_LAYOUT, HANDLE, LIMITS, gridColumns, loadLayout, saveLayout } from "../paneLayout";
import { Palette, type PaletteItem } from "../Palette";
import { ScriptEditorDialog } from "../ScriptEditorDialog";
import type { Graph, Note } from "../types";

const CHAT_W = 380; // px, colonne de l'assistant IA (à droite des propriétés)
const VIEW_KEY = "orch.schema.view";
const readView = (): "canvas" | "list" => { try { return localStorage.getItem(VIEW_KEY) === "list" ? "list" : "canvas"; } catch { return "canvas"; } };

function nextNoteId(graph: Graph): string {
  const used = new Set((graph.notes ?? []).map((n) => n.id));
  let i = 1;
  while (used.has(`note_${i}`)) i++;
  return `note_${i}`;
}

export function SchemaTab() {
  const d = useDraft();
  const { graph, setGraph, issues, editable, actions, workflows } = d;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<"canvas" | "list">(readView);
  const [scriptOpen, setScriptOpen] = useState<string | null>(null);
  const [showIssues, setShowIssues] = useState(false);
  const [layout, setLayout] = useState(loadLayout);
  const ai = useAiMode();
  const [chatOpen, setChatOpen] = useState(false);
  const showChat = chatOpen && ai.enabled && editable;
  useEffect(() => saveLayout(layout), [layout]);
  useEffect(() => { try { localStorage.setItem(VIEW_KEY, view); } catch { /* preference only */ } }, [view]);

  const selected = graph.nodes.find((n) => n.id === selectedId) ?? null;
  const selectedNote = (graph.notes ?? []).find((n) => n.id === selectedId) ?? null;
  const errors = issues.filter((i) => i.level === "error").length;
  const hasSelection = Boolean(selected || selectedNote);
  const warnings = issues.filter((i) => i.level === "warning").length;

  const add = (item: PaletteItem, pos?: { x: number; y: number }) => {
    if (!editable) return;
    if (item.kind === "note") {
      const note: Note = { id: nextNoteId(graph), text: "Note", ui: pos ?? { x: (selected?.ui?.x ?? 0), y: (selected?.ui?.y ?? 0) - 120 } };
      setGraph({ ...graph, notes: [...(graph.notes ?? []), note] });
      setSelectedId(note.id);
      return;
    }
    const base = item.kind === "action" ? defaultNode(graph, "action", actions.find((a) => a.slug === item.slug)) : defaultNode(graph, item.type);
    const node = pos ? { ...base, ui: { x: Math.round(pos.x), y: Math.round(pos.y) } } : base;
    setGraph(addNode(graph, node, pos ? undefined : (selected?.id ?? undefined)));
    setSelectedId(node.id);
  };
  const del = (ids: string[]) => {
    const noteIds = new Set(ids.filter((i) => i.startsWith("note_")));
    let g = ids.filter((i) => !noteIds.has(i)).reduce((acc, id) => removeNode(acc, id), graph);
    if (noteIds.size) g = { ...g, notes: (g.notes ?? []).filter((n) => !noteIds.has(n.id)) };
    setGraph(g);
    setSelectedId(null);
  };
  const move = (moves: { id: string; x: number; y: number }[]) => {
    // one history step for the whole drag (several nodes may be selected and moved together)
    let g = graph;
    const notes = new Map(moves.filter((m) => m.id.startsWith("note_")).map((m) => [m.id, m]));
    for (const m of moves) if (!m.id.startsWith("note_")) g = moveNode(g, m.id, m.x, m.y);
    if (notes.size) g = { ...g, notes: (g.notes ?? []).map((n) => (notes.has(n.id) ? { ...n, ui: { x: (notes.get(n.id) as { x: number }).x, y: (notes.get(n.id) as { y: number }).y } } : n)) };
    setGraph(g);
  };
  const scriptNode = graph.nodes.find((n) => n.id === scriptOpen) ?? null;

  return (
    <div className="schema-tab">
      <div className="schema-toolbar" role="toolbar" aria-label="Outils du schéma">
        <button type="button" onClick={d.undo} disabled={!d.canUndo || !editable}>Annuler</button>
        <button type="button" onClick={d.redo} disabled={!d.canRedo || !editable}>Rétablir</button>
        <button type="button" onClick={() => setGraph(autoLayout(graph))} disabled={!editable}>Organiser automatiquement</button>
        <label className="inline-label" htmlFor="goto">Aller à
          <select id="goto" value={selectedId ?? ""} onChange={(e) => setSelectedId(e.target.value || null)}>
            <option value="">—</option>{graph.nodes.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
          </select></label>
        <button type="button" aria-pressed={layout.palOpen} onClick={() => setLayout({ ...layout, palOpen: !layout.palOpen })} title="Afficher ou masquer la palette d'éléments">Palette</button>
        <button type="button" aria-pressed={layout.panOpen} onClick={() => setLayout({ ...layout, panOpen: !layout.panOpen })} title="Afficher ou masquer les propriétés de l'élément sélectionné">Propriétés</button>
        {ai.enabled && editable && <button type="button" aria-pressed={chatOpen} onClick={() => setChatOpen(!chatOpen)} title="Demander des modifications du workflow à l'assistant IA">Assistant IA</button>}
        <button type="button" onClick={() => setView(view === "canvas" ? "list" : "canvas")} aria-pressed={view === "list"}>{view === "canvas" ? "Vue liste" : "Vue schéma"}</button>
        <button type="button" className={`chip ${errors ? "chip-bad" : "chip-ok"}`} aria-expanded={showIssues} onClick={() => setShowIssues(!showIssues)}>
          {errors ? `✖ ${errors} problème(s) bloquant(s)` : warnings ? `✔ Valide · ${warnings} avertissement(s)` : "✔ Valide"}
        </button>
      </div>
      <div className={`schema-grid custom-cols${hasSelection ? " with-panel" : ""}`} style={{ gridTemplateColumns: gridColumns(layout, hasSelection) + (showChat ? ` ${HANDLE}px ${CHAT_W}px` : "") }}>
        {layout.palOpen && (
          <>
            <aside className="schema-palette" aria-label="Palette d'éléments">{editable ? <Palette actions={actions} selectedName={selected?.name ?? null} onAdd={(i) => add(i)} /> : <p className="hint">Lecture seule.</p>}</aside>
            <PaneResizer side="left" label="Redimensionner la palette" value={layout.palW} min={LIMITS.pal.min} max={LIMITS.pal.max} def={DEFAULT_LAYOUT.palW} onChange={(v) => setLayout((l) => ({ ...l, palW: v }))} />
          </>
        )}
        <div className="schema-center">
          {view === "canvas" ? (
            <CanvasView graph={graph} actions={actions} issues={issues} selectedId={selectedId} onSelect={setSelectedId} readOnly={!editable}
              onOpen={(id) => graph.nodes.find((n) => n.id === id)?.type === "script" && setScriptOpen(id)}
              onMove={(moves) => editable && moves.length > 0 && move(moves)}
              onConnect={(f, t) => editable && setGraph(connect(graph, f, t, graph.nodes.find((n) => n.id === f)?.type === "condition" ? (graph.edges.some((e) => e.from === f && e.label === "true") ? "false" : "true") : "next"))}
              onDeleteNodes={(ids) => editable && del(ids)} onDeleteEdges={(es) => editable && setGraph(es.reduce((g, e) => disconnect(g, e.from, e.to), graph))}
              onDropItem={(item, pos) => add(item, pos)} />
          ) : (
            <ListView graph={graph} actions={actions} issues={issues} selectedId={selectedId} onSelect={setSelectedId} />
          )}
          {graph.nodes.length === 0 && <p className="empty canvas-empty">Schéma vide : ajoutez un élément depuis la palette (clic ou glisser-déposer).</p>}
        </div>
        {hasSelection && layout.panOpen && <PaneResizer side="right" label="Redimensionner les propriétés" value={layout.panW} min={LIMITS.panel.min} max={LIMITS.panel.max} def={DEFAULT_LAYOUT.panW} onChange={(v) => setLayout((l) => ({ ...l, panW: v }))} />}
        {selected && layout.panOpen && (
          <aside className="schema-panel" aria-label="Propriétés de l'étape">
            <NodePanel graph={graph} node={selected} actions={actions} workflows={workflows} issues={issues} editable={editable}
              onUpdate={(p) => setGraph(updateNode(graph, selected.id, p))}
              onConnect={(to, l) => setGraph(connect(graph, selected.id, to, l))}
              onDisconnect={(to) => setGraph(disconnect(graph, selected.id, to))}
              onRelabel={(to, l) => setGraph(relabel(graph, selected.id, to, l))}
              onDuplicate={() => { const r = duplicateNode(graph, selected.id); if (r) { setGraph(r.graph); setSelectedId(r.newId); } }}
              onDelete={() => del([selected.id])} onOpenScript={() => setScriptOpen(selected.id)} />
            <button type="button" onClick={() => setSelectedId(null)}>Fermer les propriétés</button>
          </aside>
        )}
        {selectedNote && layout.panOpen && (
          <aside className="schema-panel" aria-label="Propriétés de la note">
            <section aria-label="Note" className="node-panel">
              <h2 className="np-title">Note</h2>
              <fieldset disabled={!editable} className="bare">
                <div className="field"><label htmlFor="note-text">Texte de la note</label><textarea id="note-text" rows={4} value={selectedNote.text} onChange={(e) => setGraph({ ...graph, notes: (graph.notes ?? []).map((n) => (n.id === selectedNote.id ? { ...n, text: e.target.value } : n)) })} />
                  <p className="hint">Les notes sont des annotations visuelles : elles ne sont jamais exécutées.</p></div>
                <button type="button" className="danger" onClick={() => del([selectedNote.id])}>Supprimer la note</button>
              </fieldset>
              <button type="button" onClick={() => setSelectedId(null)}>Fermer</button>
            </section>
          </aside>
        )}
        {showChat && (<><div aria-hidden="true" /><AiChat onClose={() => setChatOpen(false)} /></>)}
      </div>
      {showIssues && (
        <section aria-label="Validation" className="schema-issues">
          {issues.length === 0 ? <p>Aucun problème détecté.</p> : (
            <ul className="issue-list">
              {issues.map((i, k) => (
                <li key={k} className={`issue issue-${i.level}`}>
                  <span aria-hidden="true">{i.level === "error" ? "✖ " : "⚠ "}</span><span className="sr-only">{i.level === "error" ? "Erreur : " : "Avertissement : "}</span>
                  {i.node ? <button type="button" className="linklike" onClick={() => setSelectedId(i.node)}>{graph.nodes.find((n) => n.id === i.node)?.name ?? i.node}</button> : null}{i.node ? " — " : ""}{i.message}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      {scriptNode && <ScriptEditorDialog node={scriptNode} graph={graph} actions={actions} slug={d.slug} editable={editable} onUpdate={(p) => setGraph((g) => updateNode(g, scriptNode.id, p))} onClose={() => setScriptOpen(null)} />}
    </div>
  );
}
