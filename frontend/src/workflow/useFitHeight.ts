import { useLayoutEffect, useState, type RefObject } from "react";

const px = (v: string) => parseFloat(v) || 0;

/** Space taken, in the page, by what follows `el` (following siblings at every level up to <main>, margins and the bottom padding of <main>).
 *  The filler the shell adds to fill the window is NOT counted: only real content. */
function spaceBelow(el: HTMLElement): number {
  let below = 0;
  let n: HTMLElement | null = el;
  while (n && n.tagName !== "MAIN") {
    below += px(getComputedStyle(n).marginBottom);
    for (let s = n.nextElementSibling as HTMLElement | null; s; s = s.nextElementSibling as HTMLElement | null) {
      const cs = getComputedStyle(s);
      if (cs.position === "absolute" || cs.position === "fixed" || cs.display === "none") continue;
      below += s.getBoundingClientRect().height + px(cs.marginTop) + px(cs.marginBottom);
    }
    n = n.parentElement;
  }
  return below + (n ? px(getComputedStyle(n).paddingBottom) : 0);
}

/** Height that lets the element end at the bottom of the window, whatever real content follows it: the page then has no vertical
 *  scrollbar of its own. Measured on every render and on window resize. */
export function useFitHeight(ref: RefObject<HTMLElement | null>, min: number, enabled = true): number | undefined {
  const [h, setH] = useState<number | undefined>(undefined);
  useLayoutEffect(() => {
    if (!enabled) return;
    const fit = () => {
      const el = ref.current;
      if (!el) return;
      const top = el.getBoundingClientRect().top + window.scrollY;
      setH(Math.max(min, Math.floor(window.innerHeight - top - spaceBelow(el) - 4)));
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  });
  return enabled ? h : undefined;
}
