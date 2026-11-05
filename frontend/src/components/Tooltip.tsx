import { cloneElement, useCallback, useEffect, useId, useRef, useState, type ReactElement } from "react";
import { createPortal } from "react-dom";

type Child = ReactElement<{ "aria-describedby"?: string; onMouseEnter?: (e: React.MouseEvent) => void; onMouseLeave?: (e: React.MouseEvent) => void; onFocus?: (e: React.FocusEvent) => void; onBlur?: (e: React.FocusEvent) => void; onTouchStart?: (e: React.TouchEvent) => void; onTouchEnd?: (e: React.TouchEvent) => void }>;

/** A short label for an icon button: shown on hover, on keyboard focus and on a long touch; Escape hides it. The text is tied to the element with
 *  `aria-describedby` (the element keeps its own accessible name). Drawn in a portal so a panel never clips it. */
export function Tooltip({ text, children }: { text: string; children: Child }) {
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);
  const id = useId();
  const timer = useRef<number | undefined>(undefined);
  const holder = useRef<HTMLElement | null>(null);
  const show = useCallback((el: HTMLElement) => {
    holder.current = el;
    const r = el.getBoundingClientRect();
    setAt({ top: r.bottom + 6, left: Math.max(8, Math.min(r.left + r.width / 2, window.innerWidth - 8)) });
  }, []);
  const hide = useCallback(() => { window.clearTimeout(timer.current); setAt(null); }, []);
  useEffect(() => {
    if (!at) return;
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") hide(); };
    document.addEventListener("keydown", key);
    window.addEventListener("scroll", hide, true);
    return () => { document.removeEventListener("keydown", key); window.removeEventListener("scroll", hide, true); };
  }, [at, hide]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const p = children.props;
  return (
    <>
      {cloneElement(children, {
        "aria-describedby": at ? id : p["aria-describedby"],
        onMouseEnter: (e) => { p.onMouseEnter?.(e); show(e.currentTarget as HTMLElement); },
        onMouseLeave: (e) => { p.onMouseLeave?.(e); hide(); },
        onFocus: (e) => { p.onFocus?.(e); if ((e.currentTarget as HTMLElement).matches(":focus-visible")) show(e.currentTarget as HTMLElement); },
        onBlur: (e) => { p.onBlur?.(e); hide(); },
        onTouchStart: (e) => { p.onTouchStart?.(e); const el = e.currentTarget as HTMLElement; timer.current = window.setTimeout(() => show(el), 500); },
        onTouchEnd: (e) => { p.onTouchEnd?.(e); window.clearTimeout(timer.current); window.setTimeout(hide, 1500); },
      })}
      {at && createPortal(<span id={id} role="tooltip" className="tooltip" style={{ top: at.top, left: at.left }}>{text}</span>, document.body)}
    </>
  );
}
