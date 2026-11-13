/** Clés de liste stables dérivées du contenu : deux éléments de même texte se distinguent par leur rang d'occurrence, non par leur
 *  position dans la liste. Pour des listes d'affichage recalculées (problèmes, lignes d'un tableau) qui n'ont pas d'identifiant. */
export function keyed<T>(items: readonly T[], text: (item: T) => string): { item: T; key: string }[] {
  const seen = new Map<string, number>();
  return items.map((item) => {
    const t = text(item);
    const n = seen.get(t) ?? 0;
    seen.set(t, n + 1);
    return { item, key: `${t}#${n}` };
  });
}
