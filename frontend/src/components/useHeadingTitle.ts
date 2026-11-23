import { useEffect } from "react";
import { getBranding } from "../branding/store";

/** Titre du document = titre de niveau 1 de la page (« Mes exécutions — Fluxonata »).
 *  Dans une application à page unique le titre ne change pas tout seul : sans cela, chaque écran porte le même titre et la
 *  personne qui s'oriente avec un lecteur d'écran ou l'historique du navigateur ne distingue pas les pages. */
export function titleFor(heading: string | null | undefined): string {
  const SUFFIX = getBranding().name;
  const h = (heading ?? "").replace(/\s+/g, " ").trim();
  return h && h !== SUFFIX ? `${h} — ${SUFFIX}` : SUFFIX;
}

export function useHeadingTitle() {
  useEffect(() => {
    let queued = false;
    const apply = () => {
      queued = false;
      const next = titleFor(document.querySelector("main h1")?.textContent);
      if (document.title !== next) document.title = next;
    };
    const schedule = () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(apply);
    };
    apply();
    const observer = new MutationObserver(schedule);
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, []);
}
