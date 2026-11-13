import { useEffect, useEffectEvent } from "react";

/** Amène à l'écran et donne le focus à l'élément `targetId` quand l'adresse porte l'ancre `anchor` ET que la page est prête.
 *  Rejoué seulement si l'ancre ou l'état « prêt » change : un simple nouvel affichage ne le répète pas. */
export function useAnchorFocus(hash: string, anchor: string, ready: boolean, targetId: string) {
  const focusTarget = useEffectEvent((current: string) => {
    if (current !== anchor) return;
    const el = document.getElementById(targetId);
    el?.scrollIntoView({ block: "center" });
    el?.focus();
  });
  useEffect(() => {
    if (ready) focusTarget(hash);
  }, [hash, ready]);
}
