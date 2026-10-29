import { useCallback, useState } from "react";

interface History<T> {
  past: T[];
  present: T;
  future: T[];
}

/** Undo/redo over immutable snapshots. `set` pushes a snapshot; `replace` swaps without history (e.g. reload). */
export function useHistory<T>(initial: T, limit = 100) {
  const [h, setH] = useState<History<T>>({ past: [], present: initial, future: [] });
  const set = useCallback((next: T | ((prev: T) => T)) => {
    setH((cur) => {
      const value = typeof next === "function" ? (next as (p: T) => T)(cur.present) : next;
      if (value === cur.present) return cur;
      return { past: [...cur.past.slice(-limit + 1), cur.present], present: value, future: [] };
    });
  }, [limit]);
  const undo = useCallback(() => setH((c) => (c.past.length ? { past: c.past.slice(0, -1), present: c.past[c.past.length - 1] as T, future: [c.present, ...c.future] } : c)), []);
  const redo = useCallback(() => setH((c) => (c.future.length ? { past: [...c.past, c.present], present: c.future[0] as T, future: c.future.slice(1) } : c)), []);
  const replace = useCallback((value: T) => setH({ past: [], present: value, future: [] }), []);
  return { value: h.present, set, undo, redo, replace, canUndo: h.past.length > 0, canRedo: h.future.length > 0 };
}
