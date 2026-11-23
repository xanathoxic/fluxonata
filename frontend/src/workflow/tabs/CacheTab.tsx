import { Link } from "react-router-dom";
import { CachesTab } from "../../pages/library/CachesTab";
import { useDraft } from "../DraftContext";

/** Le cache de ce workflow : les valeurs qu'il publie sous sa clé (onglet visible quand « Publier le résultat dans le cache » est réglé). */
export function CacheTab() {
  const d = useDraft();
  if (!d.wf.cache_key) return <p className="hint">La publication dans le cache n'est pas activée pour ce workflow.</p>;
  return (
    <>
      <p className="hint">
        Clé de publication : <code>{d.wf.cache_key}</code>
        {d.wf.cache_ttl_seconds ? `, durée de vie ${d.wf.cache_ttl_seconds} s` : ", sans expiration"}
        {d.wf.cache_sensitive ? ", résultat sensible" : ""}. Se règle dans <Link to={`/workflows/${d.slug}/summary`}>Informations</Link> (onglet Cache).
      </p>
      <CachesTab exactKey={d.wf.cache_key} />
    </>
  );
}
