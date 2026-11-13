import { type KeyboardEvent, useLayoutEffect, useRef } from "react";

/** Clavier d'un groupe de boutons `role="radio"` (motif ARIA « groupe radio ») : UN seul arrêt de tabulation par groupe (le choix
 *  courant, sinon le premier), les flèches (et Début/Fin) déplacent le focus ET le choix, en sautant les boutons désactivés, avec bouclage.
 *  À poser sur le conteneur `role="radiogroup"` : `<span ref={ref} onKeyDown={onKeyDown} role="radiogroup" …>`. Le choix passe par le
 *  `onClick` existant de chaque bouton : la fonction de la commande ne change pas. */
export function useRadioGroup<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const radios = () => [...(ref.current?.querySelectorAll<HTMLButtonElement>('[role="radio"]') ?? [])].filter((r) => !r.disabled);
  useLayoutEffect(() => {
    const all = radios();
    const current = all.find((r) => r.getAttribute("aria-checked") === "true") ?? all[0];
    for (const r of ref.current?.querySelectorAll<HTMLElement>('[role="radio"]') ?? []) r.tabIndex = r === current ? 0 : -1;
  });
  const onKeyDown = (e: KeyboardEvent<T>) => {
    const all = radios();
    const at = all.indexOf(document.activeElement as HTMLButtonElement);
    if (at < 0 || all.length < 2) return;
    const target =
      e.key === "ArrowRight" || e.key === "ArrowDown" ? all[(at + 1) % all.length]
      : e.key === "ArrowLeft" || e.key === "ArrowUp" ? all[(at - 1 + all.length) % all.length]
      : e.key === "Home" ? all[0]
      : e.key === "End" ? all[all.length - 1]
      : undefined;
    if (!target) return;
    e.preventDefault();
    target.focus();
    target.click();
  };
  return { ref, onKeyDown };
}
