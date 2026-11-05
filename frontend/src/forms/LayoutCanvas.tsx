import { useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { controlIcon } from "./controls";
import { FormIcon, fieldIcon } from "./FormIcon";
import { COLS, MAX_HEIGHT } from "./layout";
import { FIELD_TYPES, type FormField, type Layout } from "./types";

const ROW_H = 74;
const GAP = 6;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
type Drag = { key: string; mode: "move" | "resize" | "vresize"; x0: number; y0: number; start: Layout; moved: boolean };

interface Props {
  /** in reading order, every field with a layout */
  fields: FormField[];
  selectedKey: string | null;
  onSelect: (key: string) => void;
  onPlace: (key: string, l: Layout) => void;
  edit: boolean;
  label: string;
}

/** The schematic grid of a section: 12 columns, one card per field at its place. Drag a card to move it, drag its right
 * edge to resize it; with the keyboard, arrows move the selected card and Shift + left/right arrows resize it. A card dropped
 * onto another one takes a row of its own: nothing is overwritten. */
export function LayoutCanvas({ fields, selectedKey, onSelect, onPlace, edit, label }: Props) {
  const grid = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const justDragged = useRef(false);
  const [ghost, setGhost] = useState<{ key: string; layout: Layout } | null>(null);
  const rows = fields.reduce((m, f) => Math.max(m, (f.layout as Layout).row + ((f.layout as Layout).height ?? 1) - 1), 0) + 1; // a spare row to drop into

  const begin = (e: ReactPointerEvent, f: FormField, mode: Drag["mode"]) => {
    if (!edit || e.button !== 0) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { key: f.key, mode, x0: e.clientX, y0: e.clientY, start: f.layout as Layout, moved: false };
    if (mode !== "move") e.stopPropagation();
  };
  const move = (e: ReactPointerEvent) => {
    const d = drag.current;
    const box = grid.current?.getBoundingClientRect();
    if (!d || !box) return;
    const dx = e.clientX - d.x0, dy = e.clientY - d.y0;
    if (!d.moved && Math.abs(dx) < 5 && Math.abs(dy) < 5) return;
    d.moved = true;
    const colsMoved = Math.round(dx / ((box.width + GAP) / COLS));
    const rowsMoved = Math.round(dy / (ROW_H + GAP));
    const s = d.start;
    const next: Layout = d.mode === "move"
      ? { ...s, row: Math.max(1, s.row + rowsMoved), col: clamp(s.col + colsMoved, 1, COLS - s.span + 1) }
      : d.mode === "resize"
        ? { ...s, span: clamp(s.span + colsMoved, 1, COLS - s.col + 1) }
        : { ...s, height: clamp((s.height ?? 1) + rowsMoved, 1, MAX_HEIGHT) };
    setGhost({ key: d.key, layout: next });
  };
  const end = () => {
    const d = drag.current;
    drag.current = null;
    if (d?.moved && ghost) { justDragged.current = true; onPlace(d.key, ghost.layout); }
    setGhost(null);
  };
  const keys = (e: KeyboardEvent, f: FormField) => {
    if (!edit) return;
    const l = f.layout as Layout;
    let next: Layout | null = null;
    if (e.shiftKey && e.key === "ArrowRight") next = { ...l, span: l.span + 1 };
    else if (e.shiftKey && e.key === "ArrowLeft") next = { ...l, span: l.span - 1 };
    else if (e.shiftKey && e.key === "ArrowDown") next = { ...l, height: (l.height ?? 1) + 1 };
    else if (e.shiftKey && e.key === "ArrowUp") next = { ...l, height: (l.height ?? 1) - 1 };
    else if (e.key === "ArrowRight") next = { ...l, col: l.col + 1 };
    else if (e.key === "ArrowLeft") next = { ...l, col: l.col - 1 };
    else if (e.key === "ArrowDown") next = { ...l, row: l.row + 1 };
    else if (e.key === "ArrowUp") next = { ...l, row: l.row - 1 };
    if (!next) return;
    e.preventDefault();
    if (next.row >= 1 && next.span >= 1 && (next.height ?? 1) >= 1) onPlace(f.key, next);
  };
  return (
    <>
      <p id="fb-lgrid-help" className="sr-only">Flèches : déplacer le champ sélectionné. Maj + flèche gauche ou droite : changer sa largeur ; Maj + flèche haut ou bas : changer sa hauteur.</p>
      <div ref={grid} className="fb-lgrid" role="group" aria-label={label} style={{ gridTemplateRows: `repeat(${rows}, ${ROW_H}px)` }}>
        {fields.map((f) => {
          const l = ghost?.key === f.key ? ghost.layout : (f.layout as Layout);
          const selected = f.key === selectedKey;
          const kind = f.type === "custom" ? (f.control?.name ?? "Contrôle personnalisé") : (FIELD_TYPES.find((t) => t.type === f.type)?.label ?? f.type);
          const name = f.label || (f.type === "divider" ? "Séparateur" : f.key);
          const req = "required" in f && f.required ? " *" : "";
          const cond = f.visible_if ? " · conditionnel" : "";
          const visible = `${name}${req} ${kind}${cond}`; // the accessible name starts with the very text shown on the card
          return (
            <button key={f.key} type="button" className={`fb-lcard${selected ? " sel" : ""}${ghost?.key === f.key ? " dragging" : ""}${f.visible_if ? " cond" : ""}`}
              style={{ gridColumn: `${l.col} / span ${l.span}`, gridRow: `${l.row} / span ${l.height ?? 1}` }} aria-pressed={selected} aria-describedby="fb-lgrid-help"
              aria-label={`${visible}, ligne ${f.layout?.row}, colonne ${f.layout?.col}, largeur ${f.layout?.span} sur ${COLS}, hauteur ${f.layout?.height ?? 1}`}
              onPointerDown={(e) => begin(e, f, "move")} onPointerMove={move} onPointerUp={end} onPointerCancel={end}
              onClick={() => { if (justDragged.current) { justDragged.current = false; return; } onSelect(f.key); }} onKeyDown={(e) => keys(e, f)}>
              <span className="fb-ticon"><FormIcon name={f.type === "custom" ? controlIcon(f.control?.icon) : fieldIcon(f.type)} size={16} /></span>
              <span className="fb-lname"><strong>{name}{req}</strong>{" "}<small>{kind}{cond}</small></span>
              {edit && <span className="fb-handle" aria-hidden="true" onPointerDown={(e) => begin(e, f, "resize")} />}
              {edit && <span className="fb-vhandle" aria-hidden="true" onPointerDown={(e) => begin(e, f, "vresize")} />}
            </button>
          );
        })}
      </div>
    </>
  );
}
