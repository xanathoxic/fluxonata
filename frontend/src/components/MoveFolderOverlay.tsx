import { useId, useState } from "react";
import { Overlay } from "./Overlay";
import { HelpTip } from "./HelpTip";

/** Choose the folder of an item (existing folders are suggested; a new path such as "Réseau/DNS" creates it). */
export function MoveFolderOverlay({ title, current, folders, error, busy, onSave, onClose }: {
  title: string; current: string; folders: string[]; error?: string; busy?: boolean; onSave: (folder: string) => void; onClose: () => void;
}) {
  const id = useId();
  const [value, setValue] = useState(current);
  const all = [...new Set(folders.filter(Boolean).flatMap((f) => f.split("/").map((_, i, a) => a.slice(0, i + 1).join("/"))))].sort();
  return (
    <Overlay variant="modal" title={title} onClose={onClose}
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="submit" form={`${id}-f`} className="primary" disabled={busy}>Déplacer</button></>}>
      <form id={`${id}-f`} onSubmit={(e) => { e.preventDefault(); onSave(value); }}>
        <div className="field">
<HelpTip>Séparez les niveaux par « / ». Un dossier vide disparaît de l'arbre.</HelpTip>
          <label htmlFor={`${id}-i`}>Dossier</label>
          <input id={`${id}-i`} list={`${id}-l`} value={value} onChange={(e) => setValue(e.target.value)} placeholder="ex. Réseau/DNS (vide : à la racine)" />
          <datalist id={`${id}-l`}>{all.map((f) => <option key={f} value={f} />)}</datalist>
        </div>
        {error && <p role="alert" className="field-error">Erreur : {error}</p>}
      </form>
    </Overlay>
  );
}
