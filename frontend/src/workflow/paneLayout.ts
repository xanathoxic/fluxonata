/** Layout of the schema editor: width of the element palette (left) and of the properties panel (right), and whether each is shown.
 *  Remembered per browser (a convenience, never required: every read/write is guarded). */
export const LIMITS = { pal: { min: 160, max: 420, def: 236 }, panel: { min: 280, max: 720, def: 340 } } as const;
export const HANDLE = 12;

export interface PaneLayout { palW: number; panW: number; palOpen: boolean; panOpen: boolean }
export const DEFAULT_LAYOUT: PaneLayout = { palW: LIMITS.pal.def, panW: LIMITS.panel.def, palOpen: true, panOpen: true };
const KEY = "orch.schema.panes";

export const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, Math.round(v)));

export function loadLayout(): PaneLayout {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "null") as Partial<PaneLayout> | null;
    if (!raw || typeof raw !== "object") return DEFAULT_LAYOUT;
    return {
      palW: typeof raw.palW === "number" ? clamp(raw.palW, LIMITS.pal.min, LIMITS.pal.max) : DEFAULT_LAYOUT.palW,
      panW: typeof raw.panW === "number" ? clamp(raw.panW, LIMITS.panel.min, LIMITS.panel.max) : DEFAULT_LAYOUT.panW,
      palOpen: raw.palOpen !== false,
      panOpen: raw.panOpen !== false,
    };
  } catch { return DEFAULT_LAYOUT; }
}

export function saveLayout(l: PaneLayout) {
  try { localStorage.setItem(KEY, JSON.stringify(l)); } catch { /* preference only */ }
}

/** CSS grid columns for [palette] [handle] [canvas] [handle] [panel], omitting what is hidden. */
export function gridColumns(l: PaneLayout, hasSelection: boolean): string {
  const cols: string[] = [];
  if (l.palOpen) cols.push(`${l.palW}px`, `${HANDLE}px`);
  cols.push("minmax(0, 1fr)");
  if (hasSelection && l.panOpen) cols.push(`${HANDLE}px`, `${l.panW}px`);
  return cols.join(" ");
}
