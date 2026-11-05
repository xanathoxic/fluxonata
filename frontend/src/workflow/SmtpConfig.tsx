import { useWsQuery } from "../lib";
import type { GraphNode } from "./types";

interface Conn { name: string; label: string; kind: string; config: { host?: string; from_address?: string } }

/** Send one text e-mail through an SMTP connection. Sending cannot be undone: test mode by default, never retried. */
export function SmtpConfig({ node, onUpdate }: { node: GraphNode; onUpdate: (patch: Partial<GraphNode>) => void }) {
  const conns = (useWsQuery<{ results: Conn[] }>("connections", "/connections/").data?.results ?? []).filter((c) => c.kind === "smtp");
  const cur = conns.find((c) => c.name === node.connection);
  return (
    <>
      <div className="field"><label htmlFor="sm-conn">Connexion (serveur SMTP)</label>
        <select id="sm-conn" value={node.connection ?? ""} onChange={(e) => onUpdate({ connection: e.target.value })}>
          <option value="">Choisir…</option>{conns.map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}</select>
        <p className="hint">{cur ? `Envoi depuis ${cur.config.from_address ?? "l'adresse de la connexion"} par ${cur.config.host}. L'expéditeur est fixé par la connexion.` : "Menu Connexions, type « Serveur SMTP » (pour Gmail : smtp.gmail.com, port 465, mot de passe d'application)."}</p></div>
      <div className="field"><label htmlFor="sm-to">Destinataire(s)</label><input id="sm-to" value={node.to ?? ""} placeholder="equipe@exemple.org, {{ inputs.item.from }}" onChange={(e) => onUpdate({ to: e.target.value })} />
        <p className="hint">Séparés par des virgules ; 20 destinataires au plus (avec la copie).</p></div>
      <div className="field"><label htmlFor="sm-cc">Copie (facultatif)</label><input id="sm-cc" value={node.cc ?? ""} onChange={(e) => onUpdate({ cc: e.target.value })} /></div>
      <div className="field"><label htmlFor="sm-subj">Objet</label><input id="sm-subj" value={node.subject ?? ""} maxLength={200} placeholder="Réclamation reçue : {{ inputs.item.subject }}" onChange={(e) => onUpdate({ subject: e.target.value })} /></div>
      <div className="field"><label htmlFor="sm-body">Message (texte)</label><textarea id="sm-body" rows={6} value={typeof node.body === "string" ? node.body : ""} onChange={(e) => onUpdate({ body: e.target.value })} />
        <p className="hint">Texte brut (100 Ko au plus), avec {"{{ expressions }}"}.</p></div>
      <div className="field"><label htmlFor="sm-mode">Mode</label>
        <select id="sm-mode" value={node.mode ?? "test"} onChange={(e) => onUpdate({ mode: e.target.value as "test" | "reel" })}>
          <option value="test">Test : aucun e-mail n'est envoyé (sortie simulée)</option><option value="reel">Réel : l'e-mail est envoyé</option></select>
        <p className="hint">Sorties : <code>steps.{node.id}.output.sent</code> (vrai si envoyé), <code>.recipients</code>, <code>.message_id</code>. Un envoi n'est jamais rejoué automatiquement.</p></div>
    </>
  );
}
