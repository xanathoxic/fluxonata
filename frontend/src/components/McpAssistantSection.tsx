import { useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { api, type ApiError } from "../api";
import { useApp } from "../context";
import { errorText, useWsQuery } from "../lib";
import { Badge, Section } from "./AdminUi";
import { ConfirmDialog } from "./ConfirmDialog";
import { Overlay } from "./Overlay";
import { SegmentedControl } from "./SegmentedControl";

interface McpToken { id: number; name: string; prefix: string; scopes: string[]; created_at: string; expires_at: string; last_used_at: string | null }
interface McpTokens { results: McpToken[]; allowed_scopes: string[] }
interface Created extends McpToken { token: string; notice: string }

const DURATIONS: [number, string][] = [[1, "1 heure"], [8, "8 heures"], [24, "24 heures"], [72, "3 jours"], [168, "7 jours"]];
const when = (iso: string) => new Date(iso).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });

/** Commandes de connexion (le jeton ne contient que des caractères sûrs pour un shell : lettres, chiffres, « _ » et « - »). */
export function connectionSnippets(url: string, token: string) {
  const header = `Authorization: Bearer ${token}`;
  const code = `claude mcp add --transport http --scope user fluxonata "${url}" --header "${header}"`;
  const desktop = JSON.stringify({
    mcpServers: {
      fluxonata: {
        command: "npx",
        args: ["-y", "mcp-remote", url, "--header", "Authorization:${FLUXONATA_AUTH}", ...(url.startsWith("http:") ? ["--allow-http"] : [])],
        env: { FLUXONATA_AUTH: `Bearer ${token}` },
      },
    },
  }, null, 2);
  const codexEnv = `export FLUXONATA_TOKEN="${token}"`;
  const codexToml = `[mcp_servers.fluxonata]\nurl = "${url}"\nbearer_token_env_var = "FLUXONATA_TOKEN"`;
  return { code, desktop, codexEnv, codexToml };
}

/** Message lisible : le détail du serveur s'il existe, sinon une phrase qui dit quoi faire (jamais « Erreur » seul). */
function failure(e: unknown): string {
  const t = errorText(e as ApiError);
  return t && t !== "Erreur" ? t : "Le serveur n'a pas pu traiter la demande. Rechargez la page ; si cela continue, prévenez un administrateur.";
}

function CopyBlock({ text, label, id }: { text: string; label: string; id?: string }) {
  const [done, setDone] = useState<boolean | null>(null);
  const copy = async () => { try { await navigator.clipboard.writeText(text); setDone(true); } catch { setDone(false); } };
  return (
    <div className="mcp-copy">
      <pre id={id} tabIndex={0} aria-label={label}>{text}</pre>
      <button type="button" onClick={() => void copy()}>Copier</button>
      {done === true && <span role="status" className="hint"> Copié.</span>}
      {done === false && <span role="alert" className="field-error"> Copie impossible : sélectionnez le texte à la main.</span>}
    </div>
  );
}

function Connect({ created, url, onClose }: { created: Created; url: string; onClose: () => void }) {
  const [client, setClient] = useState("code");
  const { code, desktop, codexEnv, codexToml } = connectionSnippets(url, created.token);
  return (
    <Overlay title="Connecter un assistant" onClose={onClose} wide description={`Jeton « ${created.name} » valable jusqu'au ${when(created.expires_at)}.`}
      footer={<button type="button" className="primary" onClick={onClose}>J'ai copié la commande, fermer</button>}>
      <p role="alert" className="field-error"><strong>Ce jeton ne sera plus jamais affiché.</strong> Copiez la commande maintenant ; il agit en votre nom, avec vos droits{created.scopes.includes("edit") ? " (lecture et brouillons)" : " (lecture seule)"}. Ne le partagez pas.</p>
      <div className="field">
        <label htmlFor="mcp-token-value">Jeton</label>
        <CopyBlock id="mcp-token-value" label="Jeton" text={created.token} />
      </div>
      <SegmentedControl label="Application à connecter" active={client} onChange={setClient}
        segments={[{ id: "code", label: "Claude Code (Linux)" }, { id: "desktop", label: "Claude Desktop" }, { id: "codex", label: "Codex" }]} />
      {client === "code" ? (
        <ol className="mcp-steps">
          <li>Dans un terminal, collez cette commande (une seule fois) :<CopyBlock label="Commande Claude Code" text={code} /></li>
          <li>Lancez <code>claude</code> depuis n'importe quel dossier, puis tapez <code>/mcp</code> : « fluxonata » doit apparaître connecté.</li>
          <li>Demandez par exemple : « Crée un workflow qui … » ; l'assistant consulte l'existant et propose des brouillons.</li>
          <li>Pour le retirer : <code>claude mcp remove fluxonata</code>.</li>
        </ol>
      ) : client === "codex" ? (
        <ol className="mcp-steps">
          <li>Dans le terminal où vous lancerez Codex, placez le jeton dans une variable d'environnement (il ne sera pas écrit dans le fichier de configuration) :<CopyBlock label="Variable Codex" text={codexEnv} /></li>
          <li>Ajoutez ce bloc à <code>~/.codex/config.toml</code> (créez le fichier s'il n'existe pas) :<CopyBlock label="Configuration Codex" text={codexToml} /></li>
          <li>Lancez <code>codex</code> depuis ce même terminal, puis tapez <code>/mcp</code> : « fluxonata » doit être listé. Pour le rendre permanent, mettez la ligne <code>export</code> dans votre profil shell, et retirez-la à l'expiration du jeton.</li>
          <li>Pour le retirer : supprimez le bloc <code>[mcp_servers.fluxonata]</code>.</li>
        </ol>
      ) : (
        <ol className="mcp-steps">
          <li>Installez Node.js (une fois), puis dans Claude Desktop : <em>Paramètres → Développeur → Modifier la configuration</em>.</li>
          <li>Ajoutez ce bloc dans <code>claude_desktop_config.json</code> (fusionnez-le avec l'existant s'il y a déjà <code>mcpServers</code>) :<CopyBlock label="Configuration Claude Desktop" text={desktop} /></li>
          <li>Quittez complètement Claude Desktop puis relancez-le : « fluxonata » apparaît dans les outils.</li>
        </ol>
      )}
      <p className="hint">Adresse du serveur : <code>{url}</code>. L'assistant ne peut jamais publier, lancer, approuver ni supprimer : il crée des brouillons et des propositions que vous relisez ici.</p>
    </Overlay>
  );
}

function CreateForm({ allowWrite, known, busy, error, onSubmit, onCancel }: { allowWrite: boolean; known: boolean; busy: boolean; error: string | null; onSubmit: (v: { name: string; hours: number; write: boolean }) => void; onCancel: () => void }) {
  const id = useId();
  const [name, setName] = useState("Mon assistant");
  const [hours, setHours] = useState(8);
  const [write, setWrite] = useState(allowWrite);
  const submit = (e: FormEvent) => { e.preventDefault(); onSubmit({ name: name.trim(), hours, write: write && (allowWrite || !known) }); };
  return (
    <Overlay variant="modal" title="Nouveau jeton pour un assistant" onClose={onCancel}
      footer={<><button type="button" onClick={onCancel}>Annuler</button><button type="submit" form={`${id}-f`} className="primary" disabled={busy || !name.trim()}>Générer le jeton</button></>}>
      <form id={`${id}-f`} onSubmit={submit}>
        <div className="field"><label htmlFor={`${id}-n`}>Nom (pour le reconnaître)</label><input id={`${id}-n`} value={name} maxLength={80} onChange={(e) => setName(e.target.value)} required /></div>
        <div className="field"><label htmlFor={`${id}-h`}>Durée de validité</label>
          <select id={`${id}-h`} value={hours} onChange={(e) => setHours(Number(e.target.value))}>{DURATIONS.map(([h, l]) => <option key={h} value={h}>{l}</option>)}</select></div>
        <div className="field">
          <label><input type="checkbox" checked={write && (allowWrite || !known)} disabled={!allowWrite && known} onChange={(e) => setWrite(e.target.checked)} /> Autoriser la création de brouillons et de propositions</label>
          <p className="hint">{!known ? "Vos droits n'ont pas pu être vérifiés : le serveur refusera cette option si votre rôle ne permet pas de modifier." : allowWrite ? "Sans cette case, l'assistant peut seulement lire ce que vous voyez." : "Votre rôle ne permet pas de modifier : le jeton sera en lecture seule."}</p>
        </div>
        {error && <p role="alert" className="field-error">{error}</p>}
      </form>
    </Overlay>
  );
}

/** « Mon compte » : générer un jeton temporaire personnel pour connecter Claude (MCP de conception, ADR 0025). */
export function McpAssistantSection() {
  const { workspace } = useApp();
  const qc = useQueryClient();
  const list = useWsQuery<McpTokens>("mcp-tokens", "/me/mcp-tokens/");
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<Created | null>(null);
  const [revoking, setRevoking] = useState<McpToken | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const base = `/workspaces/${workspace.slug}/me/mcp-tokens/`;
  const url = `${window.location.origin}/api/v1/workspaces/${workspace.slug}/mcp/design/`;
  const refresh = () => qc.invalidateQueries({ queryKey: ["mcp-tokens"] });
  const create = async (v: { name: string; hours: number; write: boolean }) => {
    setBusy(true); setError(null);
    try { setCreated(await api<Created>(base, { json: v })); setCreating(false); await refresh(); }
    catch (e) { setError(failure(e)); } finally { setBusy(false); }
  };
  const revoke = async (t: McpToken) => {
    setBusy(true);
    try { await api(`${base}${t.id}/`, { method: "DELETE" }); setRevoking(null); await refresh(); }
    catch (e) { setError(failure(e)); setRevoking(null); } finally { setBusy(false); }
  };
  const rows = list.data?.results ?? [];
  const allowWrite = (list.data?.allowed_scopes ?? []).includes("edit");
  return (
    <Section title="Assistant IA (MCP)" info={`Espace « ${workspace.name} » : connectez Claude pour qu'il prépare des workflows, formulaires et propositions en votre nom, avec vos droits.`}
      actions={<button type="button" className="primary" onClick={() => { setError(null); setCreating(true); }}>Générer un jeton</button>}>
      {list.isError ? <p role="alert" className="field-error">Impossible de charger vos jetons.</p>
        : rows.length === 0 ? <p className="hint">Aucun jeton actif. Un jeton est temporaire, personnel, et révocable à tout moment.</p>
        : (
          <table className="adm-table">
            <thead><tr><th>Nom</th><th>Accès</th><th>Expire</th><th>Dernier usage</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{rows.map((t) => (
              <tr key={t.id}>
                <td>{t.name} <span className="hint">{t.prefix}…</span></td>
                <td><Badge tone={t.scopes.includes("edit") ? "info" : "muted"}>{t.scopes.includes("edit") ? "Lecture + brouillons" : "Lecture seule"}</Badge></td>
                <td>{when(t.expires_at)}</td>
                <td>{t.last_used_at ? when(t.last_used_at) : "Jamais"}</td>
                <td><button type="button" className="danger" onClick={() => setRevoking(t)}>Révoquer</button></td>
              </tr>))}</tbody>
          </table>)}
      {creating && <CreateForm allowWrite={allowWrite} known={list.isSuccess} busy={busy} error={error} onSubmit={(v) => void create(v)} onCancel={() => setCreating(false)} />}
      {created && <Connect created={created} url={url} onClose={() => setCreated(null)} />}
      {revoking && <ConfirmDialog title="Révoquer ce jeton ?" confirmLabel="Révoquer" busy={busy} onCancel={() => setRevoking(null)} onConfirm={() => void revoke(revoking)}>
        <p>L'assistant connecté avec « {revoking.name} » perd l'accès immédiatement.</p></ConfirmDialog>}
      {!creating && !revoking && error && <p role="alert" className="field-error">{error}</p>}
    </Section>
  );
}
