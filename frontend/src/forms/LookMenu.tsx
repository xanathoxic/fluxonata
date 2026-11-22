import { useEffect, useRef, useState, type ReactNode } from "react";
import { FormIcon } from "./FormIcon";

export type Look = "simple" | "css";
const ITEMS: { id: Look; label: string; icon: "design" | "braces" }[] = [
  { id: "simple", label: "Simple", icon: "design" },
  { id: "css", label: "Avancé (CSS)", icon: "braces" },
];

/** Menu contextuel de l'onglet « Apparence » : clic droit (ou touche Menu / Maj+F10) pour choisir Simple ou Avancé.
 * Le choix n'occupe aucune ligne de l'écran tant qu'on ne l'ouvre pas. */
export function LookMenu({ look, onLook, enabled, children }: { look: Look; onLook: (v: Look) => void; enabled: boolean; children: ReactNode }) {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  const menu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!at) return;
    menu.current?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus();
    const close = (e: Event) => {
      if (e.type === "pointerdown" && menu.current?.contains(e.target as Node)) return;
      setAt(null);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAt(null);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", esc);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", esc);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [at]);
  const open = (e: React.MouseEvent) => {
    const tab = (e.target as HTMLElement).closest<HTMLElement>('[role="tab"]');
    if (!enabled || !tab || !/Apparence/.test(tab.textContent ?? "")) return;
    e.preventDefault();
    const r = tab.getBoundingClientRect();
    // au clavier (Maj+F10) la souris n'a pas de position : le menu s'ouvre sous l'onglet
    const kb = e.clientX === 0 && e.clientY === 0;
    setAt({ x: Math.min(kb ? r.left : e.clientX, window.innerWidth - 190), y: kb ? r.bottom + 4 : e.clientY });
  };
  const keys = (e: React.KeyboardEvent) => {
    const items = [...(menu.current?.querySelectorAll<HTMLElement>('[role="menuitemradio"]') ?? [])];
    const k = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      items[(k + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
    }
  };
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: relaie le clic droit de l'onglet « Apparence » ; les onglets restent des boutons
    <span className="fb-lookwrap" onContextMenu={open}>
      {children}
      {at && (
        <div ref={menu} className="fb-ctxmenu" role="menu" aria-label="Niveau de réglage de l'apparence" style={{ left: at.x, top: at.y }} onKeyDown={keys}>
          {ITEMS.map((it) => (
            <button
              key={it.id}
              type="button"
              role="menuitemradio"
              aria-checked={look === it.id}
              onClick={() => {
                onLook(it.id);
                setAt(null);
              }}
            >
              <FormIcon name={it.icon} size={16} />
              <span>{it.label}</span>
            </button>
          ))}
        </div>
      )}
    </span>
  );
}
