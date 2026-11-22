/** Longueur en pixels à l'échelle de l'interface : `--ui-s` vaut 1, ou 0,8 sur ordinateur (voir theme.css et postcss-scale.mjs). Pour les tailles écrites dans le code (styles en ligne) ; le CSS est mis à l'échelle à la construction. */
export const px = (n: number): string => `calc(${n}px * var(--ui-s))`;

/** Valeur courante de `--ui-s` (1 ou 0,8) : pour convertir des déplacements de souris (pixels réels) en unités de mise en page. */
export const scaleFactor = (): number => Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--ui-s")) || 1;
