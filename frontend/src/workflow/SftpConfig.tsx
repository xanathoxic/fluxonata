import { useWsQuery } from "../lib";
import type { GraphNode } from "./types";
import { HelpTip } from "../components/HelpTip";

interface Conn { name: string; label: string; kind: string; config: { host?: string; host_key_sha256?: string } }

/** List, read, write or delete a text file on an SFTP server. Write and delete have an effect: test mode by default. */
export function SftpConfig({ node, onUpdate }: { node: GraphNode; onUpdate: (patch: Partial<GraphNode>) => void }) {
  const conns = (useWsQuery<{ results: Conn[] }>("connections", "/connections/").data?.results ?? []).filter((c) => c.kind === "sftp");
  const cur = conns.find((c) => c.name === node.connection);
  const op = node.operation ?? "list";
  return (
    <>
      <div className="field">
<HelpTip>{cur ? (cur.config.host_key_sha256 ? `Serveur ${cur.config.host}, clé vérifiée.` : `Serveur ${cur.config.host} : clé non épinglée (l'empreinte vue est renvoyée en sortie, à reporter dans la connexion).`) : "Menu Connexions, type « Serveur SFTP » (mot de passe, ou clé privée collée comme secret)."}</HelpTip><label htmlFor="sf-conn">Connexion (serveur SFTP)</label>
        <select id="sf-conn" value={node.connection ?? ""} onChange={(e) => onUpdate({ connection: e.target.value })}>
          <option value="">Choisir…</option>{conns.map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}</select></div>
      <div className="field"><label htmlFor="sf-op">Opération</label>
        <select id="sf-op" value={op} onChange={(e) => onUpdate({ operation: e.target.value as GraphNode["operation"] })}>
          <option value="list">Lister un dossier</option><option value="read">Lire un fichier</option><option value="write">Écrire un fichier (remplace)</option><option value="delete">Supprimer un fichier</option></select></div>
      <div className="field"><label htmlFor="sf-path">Chemin</label><input id="sf-path" value={node.path ?? ""} placeholder="/depot/rapport-{{ run.id }}.txt" onChange={(e) => onUpdate({ path: e.target.value })} /></div>
      {op === "read" && <div className="field field-inline">
<HelpTip>Sinon le contenu est lu comme texte UTF-8 (256 Ko au plus). Comme fichier : 10 Mo au plus, sortie <code>steps.{node.id}.output.file</code>.</HelpTip><input id="sf-bin" type="checkbox" checked={!!node.binary} onChange={(e) => onUpdate({ binary: e.target.checked })} /><label htmlFor="sf-bin">Lire comme fichier (binaire)</label></div>}
      {op === "write" && <div className="field">
<HelpTip>La sortie <code>file</code> d'une étape précédente ; si elle est renseignée, elle remplace le contenu texte ci-dessous.</HelpTip><label htmlFor="sf-file">Fichier à écrire (facultatif)</label><input id="sf-file" value={typeof node.file === "string" ? node.file : ""} placeholder="{{ steps.telecharger.output.file }}" onChange={(e) => onUpdate({ file: e.target.value || undefined })} /></div>}
      {op === "write" && <div className="field">
<HelpTip>Texte (256 Ko au plus), avec {"{{ expressions }}"}.</HelpTip><label htmlFor="sf-content">Contenu</label><textarea id="sf-content" rows={5} value={typeof node.content === "string" ? node.content : ""} onChange={(e) => onUpdate({ content: e.target.value })} /></div>}
      <div className="field">
<HelpTip>Sorties : <code>steps.{node.id}.output.entries</code> / <code>.count</code> (lister), <code>.content</code> (lire), <code>.host_fingerprint</code>. Une écriture ou suppression n'est jamais rejouée automatiquement.</HelpTip><label htmlFor="sf-mode">Mode</label>
        <select id="sf-mode" value={node.mode ?? "test"} onChange={(e) => onUpdate({ mode: e.target.value as "test" | "reel" })}>
          <option value="test">Test : rien n'est envoyé au serveur (sortie simulée)</option><option value="reel">Réel : l'opération est exécutée</option></select></div>
    </>
  );
}
