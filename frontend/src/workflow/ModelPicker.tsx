import { useId } from "react";
import type { GraphNode } from "./types";

/** Choix du modèle de document (une étape « Modèle » du workflow) dont un fichier ou un e-mail reprend le contenu. */
export function ModelPicker({
  value,
  models,
  onChange,
  hint,
}: {
  value: string | undefined;
  models: Pick<GraphNode, "id" | "name">[];
  onChange: (expr: string | undefined) => void;
  hint: string;
}) {
  const id = useId();
  const current = models.find((m) => value === `{{ steps.${m.id}.output }}`);
  return (
    <div className="field">
      <label htmlFor={id}>Modèle de document</label>
      <select
        id={id}
        value={current ? current.id : value ? "__custom" : ""}
        onChange={(e) => onChange(e.target.value === "" ? undefined : `{{ steps.${e.target.value}.output }}`)}
      >
        <option value="">Aucun : saisir le contenu ici</option>
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.name}
          </option>
        ))}
        {value && !current && <option value="__custom">Expression : {value}</option>}
      </select>
      <p className="hint">
        {models.length === 0 ? (
          <>
            Ajoutez d'abord une étape <em>Modèle de document</em> (groupe « Données ») avant celle-ci. {hint}
          </>
        ) : (
          hint
        )}
      </p>
    </div>
  );
}
