import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { api, type ApiError } from "../../api";
import { Overlay } from "../../components/Overlay";
import { RowIcon } from "../../components/RowIcon";
import { formatDate } from "../../i18n";
import { errorText } from "../../lib";

interface Membership { workspace: string; name: string; role: string; archived: boolean }
interface UserRow {
  username: string; email: string; is_active: boolean; is_platform_admin: boolean; sso_only: boolean;
  must_change_password: boolean; last_login: string | null; memberships: Membership[];
}
interface Space { slug: string; name: string; archived: boolean }
type Secret = { username: string; temporary_password: string };

export const ROLES: [string, string][] = [
  ["viewer", "Lecteur"], ["operator", "Opérateur"], ["approver", "Approbateur"], ["designer", "Concepteur"],
  ["space_manager", "Responsable d'espace"], ["admin", "Administrateur"],
];
const roleLabel = (r: string) => ROLES.find(([k]) => k === r)?.[1] ?? r;

function TemporaryPassword({ secret, title, onClose }: { secret: Secret; title: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => { try { await navigator.clipboard.writeText(secret.temporary_password); setCopied(true); } catch { setCopied(false); } };
  return (
    <Overlay variant="modal" title={title} description="Ce mot de passe provisoire ne sera plus jamais affiché : transmettez-le à la personne maintenant." onClose={onClose}
      footer={<button type="button" className="primary" onClick={onClose}>J'ai transmis le mot de passe</button>}>
      <p>Compte : <strong className="mono">{secret.username}</strong></p>
      <pre tabIndex={0} aria-label="Mot de passe provisoire" className="mono">{secret.temporary_password}</pre>
      <p><button type="button" onClick={() => void copy()}>Copier le mot de passe</button> {copied && <span role="status" className="hint">Copié.</span>}</p>
      <p className="hint">À sa première connexion, la personne devra choisir son propre mot de passe avant de pouvoir utiliser l'application.</p>
    </Overlay>
  );
}

function SpaceRows({ spaces, rows, setRows }: { spaces: Space[]; rows: { workspace: string; role: string }[]; setRows: (r: { workspace: string; role: string }[]) => void }) {
  const id = useId();
  const free = spaces.filter((s) => !rows.some((r) => r.workspace === s.slug));
  return (
    <fieldset>
      <legend>Espaces et rôles</legend>
      {rows.length === 0 && <p className="hint">Aucun espace : la personne pourra se connecter mais n'aura accès à rien. Ajoutez-en un ou plusieurs.</p>}
      {rows.map((r, i) => (
        <div className="inline-form" key={r.workspace}>
          <div className="field"><label htmlFor={`${id}-s${i}`}>Espace</label>
            <select id={`${id}-s${i}`} value={r.workspace} onChange={(e) => setRows(rows.map((x, k) => (k === i ? { ...x, workspace: e.target.value } : x)))}>
              {[spaces.find((s) => s.slug === r.workspace), ...free].filter((s): s is Space => !!s).map((s) => <option key={s.slug} value={s.slug}>{s.name}</option>)}
            </select></div>
          <div className="field"><label htmlFor={`${id}-r${i}`}>Rôle dans {spaces.find((s) => s.slug === r.workspace)?.name ?? r.workspace}</label>
            <select id={`${id}-r${i}`} value={r.role} onChange={(e) => setRows(rows.map((x, k) => (k === i ? { ...x, role: e.target.value } : x)))}>
              {ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select></div>
          <button type="button" onClick={() => setRows(rows.filter((_, k) => k !== i))} aria-label={`Retirer l'espace ${spaces.find((s) => s.slug === r.workspace)?.name ?? r.workspace}`}>Retirer</button>
        </div>
      ))}
      <button type="button" disabled={free.length === 0} onClick={() => free[0] && setRows([...rows, { workspace: free[0].slug, role: "viewer" }])}>Ajouter un espace</button>
    </fieldset>
  );
}

function CreateOverlay({ spaces, onClose, onCreated }: { spaces: Space[]; onClose: () => void; onCreated: (s: Secret | null) => void }) {
  const qc = useQueryClient();
  const id = useId();
  const [f, setF] = useState({ username: "", email: "", password: "", must_change_password: true });
  const [rows, setRows] = useState<{ workspace: string; role: string }[]>([]);
  const create = useMutation<UserRow & { temporary_password?: string }, ApiError>({
    mutationFn: () => api("/platform/users/", { json: { username: f.username, email: f.email, password: f.password || undefined, must_change_password: f.password ? f.must_change_password : true, memberships: rows.map((r) => ({ workspace: r.workspace, role: r.role })) } }),
    onSuccess: (u) => { void qc.invalidateQueries({ queryKey: ["platform", "users"] }); onCreated(u.temporary_password ? { username: u.username, temporary_password: u.temporary_password } : null); },
  });
  return (
    <Overlay wide title="Nouvel utilisateur" description="Compte local (identifiant et mot de passe). Les comptes SSO sont créés par le fournisseur d'identité à la première connexion." onClose={onClose}
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="submit" form={`${id}-f`} className="primary" disabled={create.isPending || !f.username.trim()}>Créer l'utilisateur</button></>}>
      <form id={`${id}-f`} onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }}>
        <div className="field"><label htmlFor={`${id}-u`}>Identifiant</label><input id={`${id}-u`} className="mono" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} required autoComplete="off" />
          <p className="hint">Minuscules, chiffres, « . », « _ » ou « - » (3 à 64 caractères). Non modifiable ensuite.</p></div>
        <div className="field"><label htmlFor={`${id}-e`}>Adresse e-mail (facultatif)</label><input id={`${id}-e`} type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} autoComplete="off" /></div>
        <div className="field"><label htmlFor={`${id}-p`}>Mot de passe (facultatif)</label><input id={`${id}-p`} type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} autoComplete="new-password" />
          <p className="hint">Laissez vide pour générer un mot de passe provisoire, affiché une seule fois. Sinon : 12 caractères au moins, pas un mot de passe courant.</p></div>
        {f.password && <div className="field field-inline"><input id={`${id}-m`} type="checkbox" checked={f.must_change_password} onChange={(e) => setF({ ...f, must_change_password: e.target.checked })} /><label htmlFor={`${id}-m`}>Exiger un nouveau mot de passe à la première connexion</label></div>}
        <SpaceRows spaces={spaces} rows={rows} setRows={setRows} />
        {create.error && <p role="alert" className="field-error">Erreur : {errorText(create.error)}</p>}
      </form>
    </Overlay>
  );
}

function SpacesOverlay({ user, spaces, onClose }: { user: UserRow; spaces: Space[]; onClose: () => void }) {
  const qc = useQueryClient();
  const id = useId();
  const [add, setAdd] = useState({ workspace: "", role: "viewer" });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["platform", "users"] });
  const set = useMutation<unknown, ApiError, { workspace: string; role: string }>({ mutationFn: (b) => api(`/platform/users/${user.username}/memberships/`, { json: b }), onSuccess: refresh });
  const remove = useMutation<unknown, ApiError, string>({ mutationFn: (w) => api(`/platform/users/${user.username}/memberships/${w}/`, { method: "DELETE" }), onSuccess: refresh });
  const free = spaces.filter((s) => !s.archived && !user.memberships.some((m) => m.workspace === s.slug));
  const target = add.workspace || free[0]?.slug || "";
  return (
    <Overlay wide title={`Espaces de ${user.username}`} description="Chaque changement est appliqué tout de suite et journalisé." onClose={onClose}
      footer={<button type="button" className="primary" onClick={onClose}>Terminé</button>}>
      {user.memberships.length === 0 ? <p>Cette personne n'appartient à aucun espace.</p> : (
        <div className="table-wrap"><table><caption className="sr-only">Espaces de {user.username}</caption>
          <thead><tr><th scope="col">Espace</th><th scope="col">Rôle</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{user.memberships.map((m) => (
            <tr key={m.workspace}>
              <td>{m.name}{m.archived && " (archivé)"}</td>
              <td><label className="sr-only" htmlFor={`${id}-${m.workspace}`}>Rôle dans {m.name}</label>
                <select id={`${id}-${m.workspace}`} value={m.role} disabled={m.archived || set.isPending} onChange={(e) => set.mutate({ workspace: m.workspace, role: e.target.value })}>
                  {ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
              <td><button type="button" disabled={remove.isPending} onClick={() => remove.mutate(m.workspace)} aria-label={`Retirer ${user.username} de l'espace ${m.name}`}>Retirer</button></td>
            </tr>))}</tbody></table></div>
      )}
      <h3>Ajouter à un espace</h3>
      {free.length === 0 ? <p className="hint">Cette personne est déjà membre de tous les espaces actifs.</p> : (
        <div className="inline-form">
          <div className="field"><label htmlFor={`${id}-ns`}>Espace</label>
            <select id={`${id}-ns`} value={target} onChange={(e) => setAdd({ ...add, workspace: e.target.value })}>{free.map((s) => <option key={s.slug} value={s.slug}>{s.name}</option>)}</select></div>
          <div className="field"><label htmlFor={`${id}-nr`}>Rôle</label>
            <select id={`${id}-nr`} value={add.role} onChange={(e) => setAdd({ ...add, role: e.target.value })}>{ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
          <button type="button" className="primary" disabled={set.isPending || !target} onClick={() => { set.mutate({ workspace: target, role: add.role }); setAdd({ workspace: "", role: add.role }); }}>Ajouter</button>
        </div>
      )}
      {(set.error || remove.error) && <p role="alert" className="field-error">Erreur : {errorText(set.error ?? remove.error)}</p>}
    </Overlay>
  );
}

function EditOverlay({ user, onClose }: { user: UserRow; onClose: () => void }) {
  const qc = useQueryClient();
  const id = useId();
  const [email, setEmail] = useState(user.email);
  const save = useMutation<unknown, ApiError>({ mutationFn: () => api(`/platform/users/${user.username}/`, { method: "PATCH", json: { email } }), onSuccess: () => { void qc.invalidateQueries({ queryKey: ["platform", "users"] }); onClose(); } });
  return (
    <Overlay title={`Modifier ${user.username}`} onClose={onClose}
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="submit" form={`${id}-f`} className="primary" disabled={save.isPending}>Enregistrer</button></>}>
      <form id={`${id}-f`} onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
        <div className="field"><label htmlFor={`${id}-e`}>Adresse e-mail</label><input id={`${id}-e`} type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></div>
        <p className="hint">L'identifiant ne peut pas changer : il figure dans l'historique et le journal d'audit.</p>
        {save.error && <p role="alert" className="field-error">Erreur : {errorText(save.error)}</p>}
      </form>
    </Overlay>
  );
}

/** People of the platform (platform administrators): create, edit, deactivate, reset password, place in workspaces. */
export function UsersPanel() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const list = useQuery({ queryKey: ["platform", "users", q], queryFn: () => api<{ results: UserRow[] }>(`/platform/users/?q=${encodeURIComponent(q)}`) });
  const spaces = useQuery({ queryKey: ["platform", "spaces"], queryFn: () => api<{ results: Space[] }>("/platform/workspaces/") });
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<UserRow | null>(null);
  const [placing, setPlacing] = useState<string | null>(null);
  const [secret, setSecret] = useState<{ secret: Secret; title: string } | null>(null);
  const [confirmReset, setConfirmReset] = useState<UserRow | null>(null);
  const [confirmActive, setConfirmActive] = useState<UserRow | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["platform", "users"] });
  const reset = useMutation<Secret, ApiError, UserRow>({ mutationFn: (u) => api(`/platform/users/${u.username}/reset-password/`, { method: "POST" }), onSuccess: (s) => { setConfirmReset(null); setSecret({ secret: s, title: "Mot de passe réinitialisé" }); refresh(); } });
  const toggle = useMutation<unknown, ApiError, UserRow>({ mutationFn: (u) => api(`/platform/users/${u.username}/`, { method: "PATCH", json: { is_active: !u.is_active } }), onSuccess: () => { setConfirmActive(null); refresh(); } });
  const rows = list.data?.results ?? [];
  const placingUser = rows.find((u) => u.username === placing) ?? null;
  const spaceList = spaces.data?.results ?? [];
  return (
    <>
      <div className="toolbar-row">
        <div className="field" style={{ flex: 1 }}><label htmlFor="usr-q">Rechercher un utilisateur</label><input id="usr-q" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="identifiant ou e-mail" /></div>
        <button type="button" className="primary" onClick={() => setCreating(true)}>Nouvel utilisateur</button>
      </div>
      <p className="hint">Un compte désactivé ne peut plus se connecter (ses sessions ouvertes s'arrêtent) mais son historique est conservé : exécutions, approbations et journal gardent leur auteur. Les comptes de service se gèrent dans leur espace.</p>
      {list.isError && <p role="alert">Une erreur est survenue. Réessayez.</p>}
      <div className="table-wrap"><table><caption className="sr-only">Utilisateurs de la plate-forme</caption>
        <thead><tr><th scope="col">Utilisateur</th><th scope="col">État</th><th scope="col">Espaces</th><th scope="col">Dernière connexion</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>{rows.map((u) => (
          <tr key={u.username}>
            <td><strong className="mono">{u.username}</strong>{u.email && <><br /><span className="hint">{u.email}</span></>}</td>
            <td>
              <span className={`runner-status ${u.is_active ? "runner-online" : "runner-offline"}`}>{u.is_active ? "Actif" : "Désactivé"}</span>
              {u.is_platform_admin && <> <span className="runner-status runner-never">Admin plate-forme</span></>}
              {u.sso_only && <> <span className="runner-status runner-never">SSO</span></>}
              {u.must_change_password && <> <span className="runner-status runner-paused">Mot de passe provisoire</span></>}
            </td>
            <td>{u.memberships.length ? u.memberships.map((m) => <div key={m.workspace}>{m.name} · <span className="hint">{roleLabel(m.role)}</span></div>) : <span className="hint">Aucun espace</span>}</td>
            <td>{u.last_login ? formatDate(u.last_login) : "Jamais"}</td>
            <td className="row-actions">
              <button type="button" className="icon-btn" onClick={() => setPlacing(u.username)} aria-label={`Gérer les espaces de ${u.username}`} title="Espaces et rôles"><RowIcon name="folder" /></button>
              <button type="button" className="icon-btn" onClick={() => setEditing(u)} aria-label={`Modifier ${u.username}`} title="Modifier"><RowIcon name="edit" /></button>
              {!u.sso_only && <button type="button" className="icon-btn" onClick={() => { reset.reset(); setConfirmReset(u); }} aria-label={`Réinitialiser le mot de passe de ${u.username}`} title="Réinitialiser le mot de passe"><RowIcon name="key" /></button>}
              <button type="button" className="icon-btn" onClick={() => { toggle.reset(); setConfirmActive(u); }} aria-label={`${u.is_active ? "Désactiver" : "Réactiver"} ${u.username}`} title={u.is_active ? "Désactiver" : "Réactiver"}><RowIcon name={u.is_active ? "pause" : "play"} /></button>
            </td>
          </tr>))}</tbody></table></div>
      {list.data && rows.length === 0 && <p>Aucun utilisateur ne correspond.</p>}
      {creating && <CreateOverlay spaces={spaceList.filter((s) => !s.archived)} onClose={() => setCreating(false)} onCreated={(s) => { setCreating(false); if (s) setSecret({ secret: s, title: "Utilisateur créé" }); }} />}
      {editing && <EditOverlay user={editing} onClose={() => setEditing(null)} />}
      {placingUser && <SpacesOverlay user={placingUser} spaces={spaceList} onClose={() => setPlacing(null)} />}
      {secret && <TemporaryPassword secret={secret.secret} title={secret.title} onClose={() => setSecret(null)} />}
      {confirmReset && (
        <Overlay variant="modal" title={`Réinitialiser le mot de passe de « ${confirmReset.username} » ?`} onClose={() => setConfirmReset(null)}
          footer={<><button type="button" onClick={() => setConfirmReset(null)}>Annuler</button><button type="button" className="primary" disabled={reset.isPending} onClick={() => reset.mutate(confirmReset)}>Réinitialiser</button></>}>
          <p>L'ancien mot de passe cesse de fonctionner immédiatement. Un mot de passe provisoire est généré (affiché une seule fois) et la personne devra en choisir un nouveau à sa prochaine connexion.</p>
          {reset.error && <p role="alert" className="field-error">Erreur : {errorText(reset.error)}</p>}
        </Overlay>
      )}
      {confirmActive && (
        <Overlay variant="modal" title={`${confirmActive.is_active ? "Désactiver" : "Réactiver"} « ${confirmActive.username} » ?`} onClose={() => setConfirmActive(null)}
          footer={<><button type="button" onClick={() => setConfirmActive(null)}>Annuler</button><button type="button" className={confirmActive.is_active ? "danger" : "primary"} disabled={toggle.isPending} onClick={() => toggle.mutate(confirmActive)}>{confirmActive.is_active ? "Désactiver le compte" : "Réactiver le compte"}</button></>}>
          <p>{confirmActive.is_active ? "La personne ne pourra plus se connecter et ses sessions ouvertes s'arrêtent. Ses espaces, son historique et son nom dans le journal sont conservés ; vous pourrez réactiver le compte." : "La personne pourra de nouveau se connecter avec son mot de passe actuel, dans les espaces dont elle est encore membre."}</p>
          {toggle.error && <p role="alert" className="field-error">Erreur : {errorText(toggle.error)}</p>}
        </Overlay>
      )}
    </>
  );
}
