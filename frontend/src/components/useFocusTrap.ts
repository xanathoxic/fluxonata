import { useEffect, type RefObject } from "react";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"]), .cm-content';

/** Open dialogs, innermost last: only the top one reacts to the keyboard. */
const stack: HTMLElement[] = [];

/** Keeps Tab/Shift+Tab inside a modal dialog, focuses it on open, closes it on Escape and gives focus back to the opener.
 *  Keys are read on the document: a button that disables itself while a request runs drops focus to <body>, and Escape
 *  must still reach the dialog. */
export function useFocusTrap(ref: RefObject<HTMLElement | null>, onEscape: () => void) {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const opener = document.activeElement as HTMLElement | null;
    el.focus();
    stack.push(el);
    const onKey = (e: KeyboardEvent) => {
      if (stack[stack.length - 1] !== el) return;
      const active = document.activeElement;
      if (active && active !== document.body && !el.contains(active)) return; // focus is elsewhere on purpose
      if (e.key === "Escape" && !(e.target as HTMLElement | null)?.closest?.(".cm-content")) { e.preventDefault(); onEscape(); return; }
      if (e.key !== "Tab") return;
      const items = [...el.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((n) => n.offsetParent !== null);
      if (items.length === 0) return;
      const first = items[0] as HTMLElement, last = items[items.length - 1] as HTMLElement;
      const outside = !active || active === document.body || active === el;
      if (e.shiftKey && (active === first || outside)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (active === last || outside || (active && last.compareDocumentPosition(active) & Node.DOCUMENT_POSITION_FOLLOWING))) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      const i = stack.lastIndexOf(el);
      if (i >= 0) stack.splice(i, 1);
      opener?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- trap installed once per dialog instance
  }, []);
}
