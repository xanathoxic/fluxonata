import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { FormIcon } from "../forms/FormIcon";

interface ToastItem { id: number; text: string; undo?: () => void; tone: "ok" | "bad" }
interface Api { show: (text: string, opts?: { undo?: () => void; tone?: "ok" | "bad"; ms?: number }) => void }

const Ctx = createContext<Api>({ show: () => undefined });
export const useToast = () => useContext(Ctx);

/** Short notices that go away by themselves (`role=status`); a reversible action offers « Annuler » while the notice is shown. Errors stay longer. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);
  const drop = useCallback((id: number) => setItems((l) => l.filter((x) => x.id !== id)), []);
  const show = useCallback<Api["show"]>((text, opts) => {
    const id = ++seq.current;
    const tone = opts?.tone ?? "ok";
    setItems((l) => [...l.slice(-3), { id, text, undo: opts?.undo, tone }]);
    window.setTimeout(() => drop(id), opts?.ms ?? (opts?.undo ? 8000 : tone === "bad" ? 9000 : 4500));
  }, [drop]);
  const api = useMemo(() => ({ show }), [show]);
  return (
    <Ctx.Provider value={api}>
      {children}
      {createPortal(
        <div className="toasts" role="status" aria-live="polite">
          {items.map((x) => (
            <div key={x.id} className={`toast toast-${x.tone}`}>
              <span>{x.text}</span>
              {x.undo && <button type="button" className="btn-ghost" onClick={() => { x.undo?.(); drop(x.id); }}>Annuler</button>}
              <button type="button" className="btn-ghost" aria-label="Fermer la notification" onClick={() => drop(x.id)}><FormIcon name="close" size={14} /></button>
            </div>
          ))}
        </div>,
        document.body,
      )}
    </Ctx.Provider>
  );
}
