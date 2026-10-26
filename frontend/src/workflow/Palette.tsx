import { useState } from "react";
import type { Action } from "../api";
import { NodeIcon, type IconKind } from "./icons";
import { NODE_LABELS, type NodeType } from "./types";

export const DRAG_MIME = "application/x-fluxonata-element";
export type PaletteItem = { kind: "generic"; type: NodeType } | { kind: "action"; slug: string } | { kind: "note" };

const GENERIC: { type: NodeType; hint: string }[] = [
  { type: "script", hint: "Code Python ou JavaScript" },
  { type: "condition", hint: "Choisit une branche selon une règle" },
  { type: "wait", hint: "Pause durable" },
  { type: "approval", hint: "Demande une validation humaine" },
  { type: "throw", hint: "Arrête le workflow avec un message" },
  { type: "join", hint: "Attend plusieurs branches" },
  { type: "foreach", hint: "Répète pour chaque élément (borné)" },
  { type: "subworkflow", hint: "Réutilise un autre workflow" },
  { type: "ai", hint: "Tâche confiée à un modèle (facultatif)" },
];

interface Props {
  actions: Action[];
  selectedName: string | null;
  onAdd: (item: PaletteItem) => void;
}

function Tile({ icon, label, hint, item, onAdd }: { icon: IconKind; label: string; hint?: string; item: PaletteItem; onAdd: (i: PaletteItem) => void }) {
  return (
    <button type="button" className="tile" aria-label={`Ajouter : ${label}`} title={hint} draggable
      onDragStart={(e) => { e.dataTransfer.setData(DRAG_MIME, JSON.stringify(item)); e.dataTransfer.effectAllowed = "copy"; }}
      onClick={() => onAdd(item)}>
      <NodeIcon kind={icon} size={22} />
      <span className="tile-label" aria-hidden="true">{label}</span>
    </button>
  );
}

function Group({ title, children, open = true }: { title: string; children: React.ReactNode; open?: boolean }) {
  const [isOpen, setOpen] = useState(open);
  return (
    <section className="pal-group">
      <h3><button type="button" className="disclosure" aria-expanded={isOpen} onClick={() => setOpen(!isOpen)}><span aria-hidden="true">{isOpen ? "▾" : "▸"}</span> {title}</button></h3>
      {isOpen && children}
    </section>
  );
}

/** Element palette: icon tiles by group. Dragging is optional — clicking a tile adds it after the selected step. */
export function Palette({ actions, selectedName, onAdd }: Props) {
  const [q, setQ] = useState("");
  const f = q.trim().toLowerCase();
  const generic = GENERIC.filter((g) => !f || NODE_LABELS[g.type].toLowerCase().includes(f));
  const acts = actions.filter((a) => a.latest_version && (!f || `${a.name} ${a.description}`.toLowerCase().includes(f)));
  return (
    <section aria-label="Éléments du schéma" className="palette">
      <div className="field">
        <label htmlFor="pal-q" className="sr-only">Rechercher un élément</label>
        <input id="pal-q" type="search" placeholder="Rechercher un élément" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <p className="hint">{selectedName ? `Ajouté après « ${selectedName} ».` : "Cliquez ou glissez un élément sur le schéma."}</p>
      <Group title="Générique">
        <div className="tiles">
          {generic.map((g) => <Tile key={g.type} icon={g.type} label={NODE_LABELS[g.type]} hint={g.hint} item={{ kind: "generic", type: g.type }} onAdd={onAdd} />)}
          {(!f || "note".includes(f)) && <Tile icon="note" label="Note" hint="Annotation visuelle (non exécutée)" item={{ kind: "note" }} onAdd={onAdd} />}
        </div>
      </Group>
      <Group title={`Actions (${acts.length})`}>
        <div className="tiles">
          {acts.map((a) => <Tile key={a.slug} icon="action" label={a.name} hint={a.description} item={{ kind: "action", slug: a.slug }} onAdd={onAdd} />)}
          {acts.length === 0 && <p className="hint">Aucune action ne correspond.</p>}
        </div>
      </Group>
    </section>
  );
}
