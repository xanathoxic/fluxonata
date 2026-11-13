import { useEffect, useEffectEvent, useState } from "react";

/**
 * A text area holding one item per line (« clé | libellé | type », « valeur | libellé »).
 *
 * The text is kept AS TYPED (an empty line, a line still being written) and the items are rebuilt from it at each change. Rebuilding the
 * text from the items instead would erase the new line as soon as Enter is pressed and reformat a line while it is being typed. The text
 * starts over only when the items change from OUTSIDE (undo, another field), never because of what this field itself just sent.
 */
export function LinesField<T>({ id, rows = 4, items, toText, fromText, onItems }: { id: string; rows?: number; items: T[] | undefined; toText: (items: T[] | undefined) => string; fromText: (text: string) => T[]; onItems: (items: T[]) => void }) {
  const [text, setText] = useState(() => toText(items));
  // seul un changement des éléments venus de l'extérieur réécrit le texte : le texte tapé n'est pas un déclencheur
  const resync = useEffectEvent((external: T[] | undefined) => {
    if (JSON.stringify(fromText(text)) !== JSON.stringify(external ?? [])) setText(toText(external));
  });
  useEffect(() => {
    resync(items);
  }, [items]);
  return <textarea id={id} rows={rows} spellCheck={false} value={text} onChange={(e) => { setText(e.target.value); onItems(fromText(e.target.value)); }} />;
}
