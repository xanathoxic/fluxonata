import { useWsQuery } from "../lib";
import { JsonField } from "./HttpConfig";
import type { GraphNode } from "./types";
import { HelpTip } from "../components/HelpTip";

interface Conn { name: string; label: string; kind: string; config: { host?: string; database?: string } }

/** Read-only SQL query on a PostgreSQL connection; values go through bound parameters (%(nom)s). */
export function SqlConfig({ node, onUpdate }: { node: GraphNode; onUpdate: (patch: Partial<GraphNode>) => void }) {
  const conns = (useWsQuery<{ results: Conn[] }>("connections", "/connections/").data?.results ?? []).filter((c) => c.kind === "postgres" || c.kind === "mysql");
  const cur = conns.find((c) => c.name === node.connection);
  return (
    <>
      <div className="field">
<HelpTip>{cur ? `Serveur ${cur.config.host}, base ${cur.config.database}.` : "Menu Connexions, type « Base PostgreSQL » ou « Base MySQL / MariaDB » : utilisez un compte qui ne peut lire que ce dont le workflow a besoin."}</HelpTip><label htmlFor="sql-conn">Connexion (base PostgreSQL ou MySQL / MariaDB)</label>
        <select id="sql-conn" value={node.connection ?? ""} onChange={(e) => onUpdate({ connection: e.target.value })}>
          <option value="">Choisir…</option>{conns.map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}</select></div>
      <div className="field">
<HelpTip>Lecture seule, imposée par la base : une écriture est refusée. Pour passer une valeur, écrivez <code>%(nom)s</code> dans la requête et renseignez-la ci-dessous : elle n'est jamais collée dans le texte (pas d'injection).</HelpTip><label htmlFor="sql-q">Requête SQL (une seule instruction)</label>
        <textarea id="sql-q" rows={6} className="mono" spellCheck={false} value={node.statement ?? ""} placeholder="SELECT nom, total FROM commandes WHERE client = %(client)s" onChange={(e) => onUpdate({ statement: e.target.value })} /></div>
      <JsonField id="sql-params" label="Paramètres (JSON)" value={node.params} onChange={(v) => onUpdate({ params: v as Record<string, unknown> | undefined })} hint={'ex. {"client": "{{ inputs.client }}"}'} />
      <div className="field"><label htmlFor="sql-max">Nombre maximal de lignes (1 à 1000)</label>
        <input id="sql-max" type="number" min={1} max={1000} value={node.max_rows ?? 100} onChange={(e) => onUpdate({ max_rows: Number(e.target.value) })} /></div>
      <div className="field"><label htmlFor="sql-t">Délai maximum (secondes, 1 à 60)</label>
        <input id="sql-t" type="number" min={1} max={60} value={node.timeout_seconds ?? 15} onChange={(e) => onUpdate({ timeout_seconds: Number(e.target.value) })} /></div>
      <div className="field">
<HelpTip>Sorties : <code>steps.{node.id}.output.rows</code> (liste d'objets), <code>.count</code>, <code>.columns</code>, <code>.truncated</code>. Idéal avec « Transformer les données ».</HelpTip><label htmlFor="sql-mode">Mode</label>
        <select id="sql-mode" value={node.mode ?? "test"} onChange={(e) => onUpdate({ mode: e.target.value as "test" | "reel" })}>
          <option value="test">Test : aucune requête n'est envoyée (résultat vide)</option><option value="reel">Réel : la requête est exécutée</option></select></div>
    </>
  );
}
