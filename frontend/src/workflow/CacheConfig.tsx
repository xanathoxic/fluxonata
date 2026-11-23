import type { GraphNode } from "./types";
import { HelpTip } from "../components/HelpTip";

const asText = (v: unknown): string => (v === undefined || v === null ? "" : typeof v === "string" ? v : JSON.stringify(v, null, 2));
/** Un texte qui est du JSON (objet, liste, nombre…) est enregistré comme tel ; sinon c'est un texte, avec ses {{ expressions }}. */
const parseValue = (text: string): unknown => {
  const t = text.trim();
  if (/^[[{]/.test(t) || /^-?\d+(\.\d+)?$/.test(t) || t === "true" || t === "false" || t === "null") {
    try {
      return JSON.parse(t);
    } catch {
      return text;
    }
  }
  return text;
};
const toNumber = (text: string): number | string => (text.trim() === "" ? "" : /^\d+$/.test(text.trim()) ? Number(text.trim()) : text);

/** Lire, écrire ou supprimer une valeur du cache nommé de l'espace (par environnement) : le moyen de passer un résultat d'un workflow à un autre. */
export function CacheConfig({ node, onUpdate }: { node: GraphNode; onUpdate: (patch: Partial<GraphNode>) => void }) {
  const op = node.operation ?? "read";
  return (
    <>
      <div className="field">
        <label htmlFor="ca-op">Opération</label>
        <select id="ca-op" value={op} onChange={(e) => onUpdate({ operation: e.target.value })}>
          <option value="read">Lire une valeur</option>
          <option value="write">Écrire une valeur (remplace)</option>
          <option value="delete">Supprimer une valeur</option>
        </select>
      </div>
      <div className="field">
        <HelpTip>
          Un nom stable partagé par les workflows de l'espace (1 à 120 caractères : lettres, chiffres, . _ : / -). Le cache est séparé par environnement : un
          test n'alimente pas la production. Une expression est permise, ex. <code>inventaire:{"{{ inputs.site }}"}</code>.
        </HelpTip>
        <label htmlFor="ca-key">Clé</label>
        <input id="ca-key" value={node.key ?? ""} placeholder="inventaire-serveurs" onChange={(e) => onUpdate({ key: e.target.value })} />
      </div>
      {op === "write" && (
        <div className="field">
          <HelpTip>
            Du JSON ou une expression, ex. <code>{"{{ steps.inventaire.output.rows }}"}</code> (1 Mio au plus). Tout workflow de l'espace pourra la lire : n'y
            mettez jamais de secret.
          </HelpTip>
          <label htmlFor="ca-value">Valeur à écrire</label>
          <textarea id="ca-value" rows={4} value={asText(node.value)} onChange={(e) => onUpdate({ value: parseValue(e.target.value) })} />
        </div>
      )}
      {op === "write" && (
        <div className="field">
          <HelpTip>Au-delà, la valeur est considérée absente. Vide : elle reste jusqu'à son remplacement ou sa suppression.</HelpTip>
          <label htmlFor="ca-ttl">Durée de vie (secondes, facultatif)</label>
          <input
            id="ca-ttl"
            inputMode="numeric"
            value={node.ttl_seconds ?? ""}
            placeholder="21600 (6 h)"
            onChange={(e) => onUpdate({ ttl_seconds: toNumber(e.target.value) })}
          />
        </div>
      )}
      {op === "read" && (
        <div className="field">
          <HelpTip>Ignore une valeur plus ancienne que ce délai, même si elle n'a pas expiré. Vide : aucun âge maximal.</HelpTip>
          <label htmlFor="ca-age">Âge maximal accepté (secondes, facultatif)</label>
          <input
            id="ca-age"
            inputMode="numeric"
            value={node.max_age_seconds ?? ""}
            placeholder="3600"
            onChange={(e) => onUpdate({ max_age_seconds: toNumber(e.target.value) })}
          />
        </div>
      )}
      {op === "read" && (
        <div className="field">
          <label htmlFor="ca-missing">Si aucune valeur</label>
          <select id="ca-missing" value={node.on_missing ?? "empty"} onChange={(e) => onUpdate({ on_missing: e.target.value as "empty" | "fail" | "rebuild" })}>
            <option value="empty">Continuer sans valeur (found = faux)</option>
            <option value="fail">Échouer (branche « En cas d'erreur »)</option>
            <option value="rebuild">Reconstruire avec le workflow producteur</option>
          </select>
        </div>
      )}
      {op === "read" && node.on_missing === "rebuild" && (
        <div className="field">
          <HelpTip>
            Le workflow dont le réglage « Publier le résultat dans le cache » porte cette clé est lancé, puis la valeur est lue. Un seul lancement à la fois :
            un autre lecteur attend le même. Saisissez un objet JSON (vide : aucune entrée).
          </HelpTip>
          <label htmlFor="ca-rebuild-inputs">Entrées du workflow producteur (JSON, facultatif)</label>
          <textarea
            id="ca-rebuild-inputs"
            rows={3}
            value={asText(node.rebuild_inputs)}
            placeholder='{"site": "{{ inputs.site }}"}'
            onChange={(e) =>
              onUpdate({ rebuild_inputs: e.target.value.trim() === "" ? undefined : (parseValue(e.target.value) as GraphNode["rebuild_inputs"]) })
            }
          />
        </div>
      )}
      {op === "write" && (
        <div className="field field-inline">
          <HelpTip>
            La valeur n'est alors visible (page « Caches » et API) que des administrateurs de l'espace. Elle reste lisible par les workflows et dans la sortie
            des étapes qui la lisent : ce n'est pas un coffre pour des secrets.
          </HelpTip>
          <input id="ca-sensitive" type="checkbox" checked={node.sensitive === true} onChange={(e) => onUpdate({ sensitive: e.target.checked })} />
          <label htmlFor="ca-sensitive">Valeur sensible</label>
        </div>
      )}
      <p className="muted">
        {op === "read" && (
          <>
            Sorties : <code>steps.{node.id}.output.found</code>, <code>.value</code>, <code>.version</code>, <code>.age_seconds</code>.
          </>
        )}
        {op === "write" && (
          <>
            Sorties : <code>steps.{node.id}.output.version</code>, <code>.size_bytes</code>, <code>.expires_at</code>. Chaque écriture augmente la version.
          </>
        )}
        {op === "delete" && (
          <>
            Sortie : <code>steps.{node.id}.output.deleted</code>.
          </>
        )}
      </p>
    </>
  );
}
