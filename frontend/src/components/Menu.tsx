import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { FormIcon } from "../forms/FormIcon";

export type MenuEntry =
  | { kind?: "item"; label: string; hint?: string; icon?: ReactNode; onSelect: () => void; danger?: boolean; disabled?: boolean; checked?: boolean }
  | { kind: "heading"; label: string }
  | { kind: "separator" };

interface Props {
  /** Visible label of the trigger (also its accessible name unless `ariaLabel` is given). */
  label: ReactNode;
  ariaLabel?: string;
  entries: MenuEntry[];
  align?: "start" | "end";
  className?: string;
  title?: string;
}

const isItem = (e: MenuEntry): e is Extract<MenuEntry, { label: string; onSelect: () => void }> => e.kind === undefined || e.kind === "item";

/** Dropdown menu (WAI-ARIA menu button): arrows/Home/End move, Enter/Space select, Escape closes and returns focus, click outside closes.
 *  The list is rendered in a portal so no `overflow: hidden` ancestor can clip it. */
export function Menu({ label, ariaLabel, entries, align = "start", className = "", title }: Props) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left?: number; right?: number } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const id = useId();

  const close = (refocus: boolean) => { setOpen(false); if (refocus) btn.current?.focus(); };

  useLayoutEffect(() => {
    if (!open || !btn.current) return;
    const r = btn.current.getBoundingClientRect();
    setPos(align === "end" ? { top: r.bottom + 4, right: Math.max(8, window.innerWidth - r.right) } : { top: r.bottom + 4, left: Math.max(8, r.left) });
  }, [open, align]);

  useEffect(() => {
    if (!open) return;
    list.current?.querySelector<HTMLElement>('[role^="menuitem"]:not([disabled])')?.focus();
    const away = (e: MouseEvent) => { const t = e.target as Node; if (!list.current?.contains(t) && !btn.current?.contains(t)) setOpen(false); };
    const hide = () => setOpen(false);
    document.addEventListener("mousedown", away);
    window.addEventListener("resize", hide);
    window.addEventListener("scroll", hide, true);
    return () => { document.removeEventListener("mousedown", away); window.removeEventListener("resize", hide); window.removeEventListener("scroll", hide, true); };
  }, [open]);

  const onKey = (e: React.KeyboardEvent) => {
    const items = [...(list.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not([disabled])') ?? [])];
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(true); }
    else if (e.key === "Tab") setOpen(false);
    else if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
    else if (e.key === "Home") { e.preventDefault(); items[0]?.focus(); }
    else if (e.key === "End") { e.preventDefault(); items[items.length - 1]?.focus(); }
  };

  return (
    <>
      <button ref={btn} type="button" className={`menu-trigger ${className}`.trim()} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined} aria-label={ariaLabel} title={title}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => { if (e.key === "ArrowDown" && !open) { e.preventDefault(); setOpen(true); } }}>
        {label}<FormIcon name="down" size={14} className="menu-caret" />
      </button>
      {open && createPortal(
        <div ref={list} id={id} role="menu" className="menu-list" aria-label={ariaLabel ?? (typeof label === "string" ? label : undefined)} onKeyDown={onKey}
          style={pos ? { top: pos.top, left: pos.left, right: pos.right } : { visibility: "hidden" }}>
          {entries.map((e, k) => {
            if (e.kind === "separator") return <hr key={k} role="separator" className="menu-sep" />;
            if (e.kind === "heading") return <div key={k} role="presentation" className="menu-heading">{e.label}</div>;
            if (!isItem(e)) return null;
            const role = e.checked === undefined ? "menuitem" : "menuitemcheckbox";
            return (
              <button key={k} type="button" role={role} aria-checked={e.checked} disabled={e.disabled} tabIndex={-1} className={`menu-item${e.danger ? " menu-danger" : ""}`}
                onClick={() => { close(true); e.onSelect(); }}>
                <span className="menu-ico" aria-hidden="true">{e.checked === undefined ? e.icon : e.checked ? "✓" : ""}</span>
                <span className="menu-txt">{e.label}{e.hint && <small>{e.hint}</small>}</span>
              </button>
            );
          })}
        </div>,
        document.body,
      )}
    </>
  );
}
