import { useState } from "react";
import type { Action, Integration } from "../api";
import { BrandIcon } from "../components/BrandIcon";
import { NodeIcon, type IconKind } from "./icons";
import { NODE_LABELS, type NodeType } from "./types";

export const DRAG_MIME = "application/x-fluxonata-element";
export type PaletteItem = { kind: "generic"; type: NodeType } | { kind: "action"; slug: string } | { kind: "integration"; slug: string } | { kind: "note" };

const GENERIC: { type: NodeType; hint: string; cat: StepCat }[] = [
  { type: "script", hint: "Code Python ou JavaScript", cat: "data" },
  { type: "transform", hint: "Filtrer, trier, regrouper, fusionner des listes (sans code)", cat: "data" },
  { type: "ldap", hint: "Cherche dans un annuaire LDAP / Active Directory (lecture seule)", cat: "comm" },
  { type: "sql", hint: "Lit des données dans une base PostgreSQL (lecture seule)", cat: "comm" },
  { type: "http", hint: "Appelle un service web via une connexion", cat: "comm" },
  { type: "smtp", hint: "Envoie un e-mail texte par un serveur SMTP (mode test par défaut)", cat: "comm" },
  { type: "sftp", hint: "Liste, lit, écrit ou supprime un fichier texte sur un serveur SFTP", cat: "comm" },
  { type: "condition", hint: "Choisit une branche selon une règle", cat: "flow" },
  { type: "switch", hint: "Choisit parmi plusieurs cas (le premier qui correspond)", cat: "flow" },
  { type: "foreach", hint: "Répète pour chaque élément (borné)", cat: "flow" },
  { type: "while", hint: "Rappelle un workflow tant qu'une condition est vraie (borné)", cat: "flow" },
  { type: "join", hint: "Attend plusieurs branches", cat: "flow" },
  { type: "race", hint: "Continue dès que la première branche arrive (les autres sont annulées)", cat: "flow" },
  { type: "wait", hint: "Pause durable", cat: "flow" },
  { type: "subworkflow", hint: "Réutilise un autre workflow", cat: "flow" },
  { type: "throw", hint: "Arrête le workflow avec un message", cat: "flow" },
  { type: "approval", hint: "Demande une validation humaine", cat: "human" },
  { type: "ai", hint: "Tâche confiée à un modèle (facultatif)", cat: "human" },
  { type: "agent", hint: "Un modèle choisit parmi des actions autorisées (borné, validé)", cat: "human" },
];
type StepCat = "flow" | "data" | "comm" | "human";
const CATS: { id: StepCat; title: string }[] = [
  { id: "flow", title: "Logique et flux" },
  { id: "data", title: "Données et code" },
  { id: "comm", title: "Échanges avec l'extérieur" },
  { id: "human", title: "Humain et IA" },
];
type Section = "steps" | "integrations" | "actions";

interface Props {
  actions: Action[];
  integrations?: Integration[];
  selectedName: string | null;
  onAdd: (item: PaletteItem) => void;
}

function Tile({ icon, lead, label, hint, item, onAdd }: { icon: IconKind; lead?: React.ReactNode; label: string; hint?: string; item: PaletteItem; onAdd: (i: PaletteItem) => void }) {
  return (
    <button type="button" className="tile" aria-label={`Ajouter : ${label}`} title={hint} draggable
      onDragStart={(e) => { e.dataTransfer.setData(DRAG_MIME, JSON.stringify(item)); e.dataTransfer.effectAllowed = "copy"; }}
      onClick={() => onAdd(item)}>
      {lead ?? <NodeIcon kind={icon} size={22} />}
      <span className="tile-label" aria-hidden="true">{label}</span>
    </button>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="pal-group">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

/** Element palette. Three sections (steps / integrations / actions) behind a segmented control so only one list is on screen;
 *  a search looks through all of them at once. Dragging is optional — clicking a tile adds it after the selected step. */
export function Palette({ actions, integrations = [], selectedName, onAdd }: Props) {
  const [q, setQ] = useState("");
  const [section, setSection] = useState<Section>("steps");
  const f = q.trim().toLowerCase();
  const generic = GENERIC.filter((g) => !f || `${NODE_LABELS[g.type]} ${g.hint}`.toLowerCase().includes(f));
  const showNote = !f || "note annotation".includes(f);
  const acts = actions.filter((a) => a.latest_version && (!f || `${a.name} ${a.description}`.toLowerCase().includes(f)));
  const integs = integrations.filter((i) => !f || `${i.name} ${i.category} ${i.description} ${i.operations.map((o) => o.name).join(" ")}`.toLowerCase().includes(f));
  const nSteps = generic.length + (showNote ? 1 : 0);
  const show = (s: Section) => f !== "" || section === s;
  const sections: { id: Section; label: string; n: number }[] = [
    { id: "steps", label: "Étapes", n: nSteps },
    ...(integrations.length > 0 ? [{ id: "integrations" as const, label: "Intégrations", n: integs.length }] : []),
    { id: "actions", label: "Actions", n: acts.length },
  ];
  const tile = (g: typeof GENERIC[number]) => <Tile key={g.type} icon={g.type} label={NODE_LABELS[g.type]} hint={g.hint} item={{ kind: "generic", type: g.type }} onAdd={onAdd} />;
  return (
    <section aria-label="Éléments du schéma" className="palette">
      <div className="field">
        <label htmlFor="pal-q" className="sr-only">Rechercher un élément</label>
        <input id="pal-q" type="search" placeholder="Rechercher un élément" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {f === "" && (
        <div className="seg" role="group" aria-label="Type d'élément">
          {sections.map((s) => <button key={s.id} type="button" className="seg-btn" aria-pressed={section === s.id} onClick={() => setSection(s.id)}>{s.label}<span className="seg-n">{s.id === "steps" ? "" : s.n}</span></button>)}
        </div>
      )}
      <p className="hint">{selectedName ? `Ajouté après « ${selectedName} ».` : "Cliquez ou glissez un élément sur le schéma."}</p>
      {show("steps") && (
        <>
          {CATS.map((c) => {
            const items = generic.filter((g) => g.cat === c.id);
            return items.length ? <Group key={c.id} title={c.title}><div className="tiles">{items.map(tile)}</div></Group> : null;
          })}
          {showNote && <Group title="Annotation"><div className="tiles"><Tile icon="note" label="Note" hint="Annotation visuelle (non exécutée)" item={{ kind: "note" }} onAdd={onAdd} /></div></Group>}
        </>
      )}
      {show("integrations") && integs.length > 0 && (
        <Group title={f ? `Intégrations (${integs.length})` : "Intégrations"}>
          <div className="tiles">{integs.map((i) => <Tile key={i.slug} icon="http" lead={<BrandIcon slug={i.slug} name={i.name} size={24} />} label={i.name} hint={`${i.category} — ${i.description}`} item={{ kind: "integration", slug: i.slug }} onAdd={onAdd} />)}</div>
        </Group>
      )}
      {show("actions") && acts.length > 0 && (
        <Group title={f ? `Actions (${acts.length})` : "Actions du catalogue"}>
          <div className="tiles">{acts.map((a) => <Tile key={a.slug} icon="action" label={a.name} hint={a.description} item={{ kind: "action", slug: a.slug }} onAdd={onAdd} />)}</div>
        </Group>
      )}
      {((f && nSteps + integs.length + acts.length === 0) || (!f && sections.find((s) => s.id === section)?.n === 0)) && <p className="hint">Aucun élément ne correspond.</p>}
    </section>
  );
}
