import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiError } from "../api";

/** `invalid`: the server refused the draft as it is (400): nothing is lost locally, and the next edit tries again. */
export type AutosaveState = "saved" | "dirty" | "saving" | "conflict" | "error" | "invalid";

interface Options<T> {
  /** Where the unsaved copy is kept in this browser (one key per object). */
  storageKey: string;
  /** The content being edited; `null` until loaded. */
  value: T | null;
  /** What the server holds. A NEW object means « (re)loaded »: the revision and the saved state restart from it. */
  baseline: { value: T; revision: number } | null;
  /** Sends the content with the revision it was based on; returns the new revision. Throws an `ApiError`. */
  save: (value: T, revision: number) => Promise<{ revision: number }>;
  /** The unsaved local copy found at load time, accepted by the person. */
  onRecover: (value: T) => void;
  enabled?: boolean;
  /** Nothing is scheduled while paused (publishing); `flush` still works. */
  paused?: boolean;
  delay?: number;
}

const readLocal = <T,>(key: string): T | null => { try { const raw = localStorage.getItem(key); return raw ? (JSON.parse(raw) as T) : null; } catch { return null; } };
const writeLocal = (key: string, v: unknown) => { try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* a convenience: ignore */ } };
const clearLocal = (key: string) => { try { localStorage.removeItem(key); } catch { /* ignore */ } };

/** Debounced autosave of a draft with an optimistic revision, shared by the form and control editors.
 *  - ONE request at a time: an edit made while a save is running is sent right after it, with the revision the server just returned.
 *  - A 409 stops everything until the person chooses (`reload` on their side, or `overwrite` here); nothing is overwritten silently.
 *  - A 400 (draft refused as it is) is shown as « invalid » and kept locally; any other failure retries every 5 s.
 *  - The unsaved content is mirrored in localStorage and offered back at the next load; closing the tab while unsaved asks for confirmation;
 *    leaving the page sends a last save. */
export function useAutosave<T>({ storageKey, value, baseline, save, onRecover, enabled = true, paused = false, delay = 1200 }: Options<T>) {
  const [state, setState] = useState<AutosaveState>("saved");
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const [message, setMessage] = useState("");
  const [recover, setRecover] = useState<T | null>(null);

  const latest = useRef(value);
  latest.current = value;
  const saveFn = useRef(save);
  saveFn.current = save;
  const keyRef = useRef(storageKey);
  keyRef.current = storageKey;
  const stateRef = useRef<AutosaveState>("saved");
  const set = useCallback((s: AutosaveState) => { stateRef.current = s; setState(s); }, []);
  const revision = useRef(1);
  const savedJson = useRef("");
  const inflight = useRef<Promise<boolean> | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const retry = useRef<number | undefined>(undefined);
  const conflictRevision = useRef<number | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    if (!baseline) return;
    revision.current = baseline.revision;
    savedJson.current = JSON.stringify(baseline.value);
    conflictRevision.current = null;
    window.clearTimeout(timer.current); window.clearTimeout(retry.current);
    set("saved"); setMessage("");
    const local = readLocal<T>(keyRef.current);
    setRecover(local !== null && JSON.stringify(local) !== savedJson.current ? local : null);
  }, [baseline, set]);

  const run = useCallback((): Promise<boolean> => {
    if (inflight.current) return inflight.current;
    const p = (async () => {
      for (;;) {
        const v = latest.current;
        if (v === null) return true;
        const json = JSON.stringify(v);
        if (json === savedJson.current) { if (alive.current) set("saved"); return true; }
        if (alive.current) set("saving");
        try {
          const r = await saveFn.current(v, revision.current);
          revision.current = r.revision;
          savedJson.current = json;
          if (alive.current) { setSavedAt(new Date()); setMessage(""); }
          if (JSON.stringify(latest.current) === json) clearLocal(keyRef.current);
        } catch (e) {
          const err = e as ApiError;
          if (!alive.current) return false;
          if (err.status === 409) {
            conflictRevision.current = (err.details as { current_revision?: number } | null)?.current_revision ?? null;
            set("conflict"); setMessage("");
          } else if (err.status === 400) {
            set("invalid"); setMessage(typeof err.message === "string" ? err.message : "");
          } else {
            set("error"); setMessage("");
            retry.current = window.setTimeout(() => { void run(); }, 5000);
          }
          return false;
        }
      }
    })().finally(() => { inflight.current = null; });
    inflight.current = p;
    return p;
  }, [set]);

  // an edit: mirror it locally, mark it, schedule the save
  useEffect(() => {
    if (!enabled || value === null || !baseline) return;
    const json = JSON.stringify(value);
    if (json === savedJson.current) {
      if (stateRef.current === "dirty" || stateRef.current === "invalid") set("saved"); // back to what the server holds
      return;
    }
    writeLocal(keyRef.current, value);
    if (stateRef.current === "conflict") return;
    if (stateRef.current !== "saving") set("dirty");
    if (paused) return;
    window.clearTimeout(retry.current);
    timer.current = window.setTimeout(() => { void run(); }, delay);
    return () => window.clearTimeout(timer.current);
  }, [value, enabled, paused, delay, baseline, run, set]);

  useEffect(() => {
    const h = (e: BeforeUnloadEvent) => { if (stateRef.current !== "saved") e.preventDefault(); };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, []);

  // leaving the page (route change): one last attempt, unless a conflict is waiting for a decision
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      window.clearTimeout(timer.current); window.clearTimeout(retry.current);
      if (enabled && stateRef.current !== "saved" && stateRef.current !== "conflict" && stateRef.current !== "invalid") void run();
    };
  }, [enabled, run]);

  /** Saves what is pending now and resolves true when the server holds the current content (e.g. before publishing). */
  const flush = useCallback(async () => {
    window.clearTimeout(timer.current); window.clearTimeout(retry.current);
    if (stateRef.current === "conflict") return false;
    return run();
  }, [run]);
  /** After a conflict: keep my content on top of the server's current revision. */
  const overwrite = useCallback(async () => {
    if (conflictRevision.current === null) return false;
    revision.current = conflictRevision.current;
    conflictRevision.current = null;
    set("dirty");
    return run();
  }, [run, set]);
  const acceptRecovery = useCallback(() => { setRecover((r) => { if (r !== null) onRecover(r); return null; }); }, [onRecover]);
  const discardRecovery = useCallback(() => { clearLocal(keyRef.current); setRecover(null); }, []);
  const discardLocal = useCallback(() => clearLocal(keyRef.current), []);

  return { state, savedAt, message, recover, flush, overwrite, acceptRecovery, discardRecovery, discardLocal, dirty: state !== "saved" };
}
