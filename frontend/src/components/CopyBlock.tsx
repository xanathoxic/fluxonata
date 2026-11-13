import { useState } from "react";
import { ReadRegion } from "./ReadRegion";

/** Un texte à copier tel quel (clé, commande) : sélectionnable, avec un bouton « Copier » dont le résultat est annoncé. */
export function CopyBlock({ text, label, id }: { text: string; label: string; id?: string }) {
  const [done, setDone] = useState<boolean | null>(null);
  const copy = async () => { try { await navigator.clipboard.writeText(text); setDone(true); } catch { setDone(false); } };
  return (
    <div className="mcp-copy">
      <ReadRegion id={id} label={label}>{text}</ReadRegion>
      <button type="button" onClick={() => void copy()}>Copier</button>
      {done === true && <span role="status" className="hint"> Copié.</span>}
      {done === false && <span role="alert" className="field-error"> Copie impossible : sélectionnez le texte à la main.</span>}
    </div>
  );
}
