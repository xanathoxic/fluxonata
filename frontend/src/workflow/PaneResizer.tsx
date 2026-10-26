import { useRef } from "react";

/** Vertical splitter between two panes: drag with the pointer, or focus it and use the arrow keys (Shift = larger steps);
 *  double-click or Home restores the default width. `side` is the pane it resizes: dragging right grows the left pane. */
export function PaneResizer({ side, value, min, max, label, def, onChange }: {
  side: "left" | "right"; value: number; min: number; max: number; label: string; def: number; onChange: (v: number) => void;
}) {
  const start = useRef<{ x: number; v: number } | null>(null);
  const grow = side === "left" ? 1 : -1; // moving the pointer right grows the left pane, shrinks the right one
  return (
    <div
      role="separator" aria-orientation="vertical" aria-label={label} aria-valuenow={value} aria-valuemin={min} aria-valuemax={max} tabIndex={0}
      className="pane-resizer" title="Glisser pour redimensionner ; double-clic : largeur par défaut"
      onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); start.current = { x: e.clientX, v: value }; }}
      onPointerMove={(e) => { if (start.current) onChange(Math.min(max, Math.max(min, Math.round(start.current.v + grow * (e.clientX - start.current.x))))); }}
      onPointerUp={(e) => { start.current = null; e.currentTarget.releasePointerCapture(e.pointerId); }}
      onPointerCancel={() => { start.current = null; }}
      onDoubleClick={() => onChange(def)}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 48 : 16;
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
          e.preventDefault();
          onChange(Math.min(max, Math.max(min, value + grow * (e.key === "ArrowRight" ? step : -step))));
        } else if (e.key === "Home") { e.preventDefault(); onChange(def); }
      }}
    />
  );
}
