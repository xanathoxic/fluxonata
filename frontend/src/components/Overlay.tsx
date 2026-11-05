import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useFocusTrap } from "./useFocusTrap";

interface Props {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** "drawer" slides in from the right (forms, details); "modal" is centred (short confirmations). */
  variant?: "drawer" | "modal";
  footer?: ReactNode;
  description?: string;
  wide?: boolean;
}

let openCount = 0;
/** While an overlay is open the rest of the app is inert: no focus, no screen-reader navigation, no clicks behind the dialog. */
function useInertBackground() {
  useEffect(() => {
    const root = document.getElementById("root");
    openCount++;
    root?.setAttribute("inert", "");
    return () => { openCount--; if (openCount === 0) root?.removeAttribute("inert"); };
  }, []);
}

/** Accessible overlay: role=dialog, focus trapped, Escape closes, focus returns to the opener. Keeps long forms off the page. */
export function Overlay({ title, onClose, children, variant = "drawer", footer, description, wide }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  useInertBackground(); // declared before the focus trap: inert is lifted before focus returns to the opener
  useFocusTrap(ref, onClose);
  return createPortal(
    <div className={`overlay-backdrop overlay-${variant}${wide ? " overlay-wide" : ""}`} onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={`${id}-t`} aria-describedby={description ? `${id}-d` : undefined} tabIndex={-1} className="overlay-panel">
        <header className="overlay-head">
          <div>
            <h2 id={`${id}-t`}>{title}</h2>
            {description && <p id={`${id}-d`} className="hint">{description}</p>}
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Fermer">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </header>
        <div className="overlay-body">{children}</div>
        {footer && <footer className="overlay-foot">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}
