import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** A small « ? » that opens a balloon with the help text: explanations stay one click away instead of cluttering the page.
 *  The balloon is drawn in a portal (never clipped by a panel) and closes on Escape, on a click elsewhere or on the button. */
export function HelpTip({ children, label = "Aide", caption }: { children: ReactNode; label?: string; caption?: string }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const btn = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLSpanElement>(null);
  const id = useId();

  const place = () => {
    const r = btn.current?.getBoundingClientRect();
    if (!r) return;
    const width = Math.min(320, window.innerWidth - 24);
    setPos({ top: r.bottom + 8, left: Math.max(12, Math.min(r.right - width, window.innerWidth - width - 12)) });
  };
  useEffect(() => {
    if (!open) return;
    place();
    const close = (e: Event) => {
      const t = e.target as Node;
      if (pop.current?.contains(t) || btn.current?.contains(t)) return;
      setOpen(false);
    };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); btn.current?.focus(); e.stopPropagation(); } };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", key, true);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", key, true);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);

  return (
    <span className="help">
      <button ref={btn} type="button" className={caption ? "help-pill" : "help-btn"} aria-label={caption ? undefined : label} aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen((o) => !o)}>{caption ? <><span aria-hidden="true" className="help-q">?</span> {caption}</> : "?"}</button>
      {open && createPortal(
        <span ref={pop} id={id} role="note" className="help-pop" style={{ top: pos.top, left: pos.left }}>{children}</span>,
        document.body,
      )}
    </span>
  );
}
