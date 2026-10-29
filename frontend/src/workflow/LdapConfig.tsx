import { useWsQuery } from "../lib";
import { JsonField } from "./HttpConfig";
import type { GraphNode } from "./types";
import { HelpTip } from "../components/HelpTip";

interface Conn { name: string; label: string; kind: string; config: { host?: string; base_dn?: string } }

/** Read-only search in an LDAP / Active Directory directory; filter values go through escaped {nom} placeholders. */
export function LdapConfig({ node, onUpdate }: { node: GraphNode; onUpdate: (patch: Partial<GraphNode>) => void }) {
  const conns = (useWsQuery<{ results: Conn[] }>("connections", "/connections/").data?.results ?? []).filter((c) => c.kind === "ldap");
  const cur = conns.find((c) => c.name === node.connection);
  return (
    <>
      <div className="field">
<HelpTip>{cur ? `Serveur ${cur.config.host}, racine ${cur.config.base_dn}.` : "Menu Connexions, type « Annuaire LDAP » : utilisez un compte de service en lecture seule."}</HelpTip><label htmlFor="ld-conn">Connexion (annuaire LDAP)</label>
        <select id="ld-conn" value={node.connection ?? ""} onChange={(e) => onUpdate({ connection: e.target.value })}>
          <option value="">Choisir…</option>{conns.map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}</select></div>
      <div className="field">
<HelpTip>Écrivez <code>{"{nom}"}</code> pour une valeur à renseigner ci-dessous : elle est <strong>échappée</strong> (un « * » saisi n'est jamais un joker, pas d'injection de filtre).</HelpTip><label htmlFor="ld-filter">Filtre LDAP</label>
        <input id="ld-filter" className="mono" list="expr-paths" value={node.filter ?? ""} placeholder="(&(objectClass=person)(mail={mail}))" onChange={(e) => onUpdate({ filter: e.target.value })} /></div>
      <JsonField id="ld-params" label="Valeurs du filtre (JSON)" value={node.params} onChange={(v) => onUpdate({ params: v as Record<string, unknown> | undefined })} hint={'ex. {"mail": "{{ inputs.email }}"}'} />
      <div className="field"><label htmlFor="ld-attrs">Attributs à renvoyer (séparés par des virgules ; vide = tous)</label>
        <input id="ld-attrs" value={(node.attributes ?? []).join(", ")} placeholder="cn, mail, uid" onChange={(e) => onUpdate({ attributes: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} /></div>
      <div className="field"><label htmlFor="ld-base">Racine de recherche (vide = celle de la connexion)</label><input id="ld-base" value={node.base ?? ""} placeholder="ou=Personnes,dc=exemple,dc=org" onChange={(e) => onUpdate({ base: e.target.value })} /></div>
      <div className="field"><label htmlFor="ld-scope">Portée</label>
        <select id="ld-scope" value={node.scope ?? "sub"} onChange={(e) => onUpdate({ scope: e.target.value as "sub" | "one" | "base" })}><option value="sub">Toute l'arborescence</option><option value="one">Un seul niveau</option><option value="base">L'entrée elle-même</option></select></div>
      <div className="field"><label htmlFor="ld-max">Nombre maximal de résultats (1 à 1000)</label>
        <input id="ld-max" type="number" min={1} max={1000} value={node.max_rows ?? 100} onChange={(e) => onUpdate({ max_rows: Number(e.target.value) })} /></div>
      <div className="field">
<HelpTip>Sorties : <code>steps.{node.id}.output.rows</code> (chaque ligne a <code>dn</code> et ses attributs), <code>.count</code>, <code>.truncated</code>. Lecture seule : aucune modification n'est jamais envoyée.</HelpTip><label htmlFor="ld-mode">Mode</label>
        <select id="ld-mode" value={node.mode ?? "test"} onChange={(e) => onUpdate({ mode: e.target.value as "test" | "reel" })}>
          <option value="test">Test : aucune recherche n'est envoyée (résultat vide)</option><option value="reel">Réel : la recherche est exécutée</option></select></div>
    </>
  );
}
