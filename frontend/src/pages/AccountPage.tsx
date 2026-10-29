import { useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { api, type ApiError } from "../api";
import { Avatar, Badge, Section } from "../components/AdminUi";
import { Logo } from "../components/Logo";
import { PageHeader } from "../components/PageHeader";
import { errorText } from "../lib";

/** Change one's own password. `forced`: signed in with a temporary password, nothing else is available until this is done. */
export function ChangePasswordForm({ forced, onDone }: { forced?: boolean; onDone?: () => void }) {
  const qc = useQueryClient();
  const id = useId();
  const [f, setF] = useState({ current: "", next: "", again: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const mismatch = f.again !== "" && f.next !== f.again;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (f.next !== f.again) { setError("Les deux nouveaux mots de passe ne sont pas identiques."); return; }
    setBusy(true); setError(null);
    try {
      await api("/auth/password/", { json: { current_password: f.current, new_password: f.next } });
      setDone(true); setF({ current: "", next: "", again: "" });
      await qc.invalidateQueries({ queryKey: ["me"] });
      onDone?.();
    } catch (err) {
      const d = (err as ApiError).details as Record<string, string[] | string> | null;
      const parts = d ? Object.values(d).flat().map(String) : [];
      setError(parts.length ? parts.join(" ") : errorText(err));
    } finally { setBusy(false); }
  };
  return (
    <form onSubmit={(e) => void submit(e)} aria-labelledby={`${id}-t`}>
      <h2 id={`${id}-t`}>{forced ? "Choisissez votre mot de passe" : "Changer mon mot de passe"}</h2>
      {forced && <p>Vous êtes connecté avec un mot de passe provisoire. Choisissez le vôtre pour continuer.</p>}
      <div className="field"><label htmlFor={`${id}-c`}>{forced ? "Mot de passe provisoire" : "Mot de passe actuel"}</label><input id={`${id}-c`} type="password" autoComplete="current-password" value={f.current} onChange={(e) => setF({ ...f, current: e.target.value })} required /></div>
      <div className="field"><label htmlFor={`${id}-n`}>Nouveau mot de passe</label><input id={`${id}-n`} type="password" autoComplete="new-password" value={f.next} onChange={(e) => setF({ ...f, next: e.target.value })} required aria-describedby={`${id}-h`} />
        <p id={`${id}-h`} className="hint">12 caractères au moins, différent de l'actuel, pas un mot de passe courant ni uniquement des chiffres.</p></div>
      <div className="field"><label htmlFor={`${id}-a`}>Confirmer le nouveau mot de passe</label><input id={`${id}-a`} type="password" autoComplete="new-password" value={f.again} onChange={(e) => setF({ ...f, again: e.target.value })} required aria-invalid={mismatch} /></div>
      {error && <p role="alert" className="field-error">Erreur : {error}</p>}
      {done && <p role="status">Mot de passe modifié. Vos autres sessions ont été fermées.</p>}
      <button type="submit" className="primary" disabled={busy || !f.current || !f.next}>Enregistrer le mot de passe</button>
    </form>
  );
}

export function ForcedPasswordPage({ username, onLogout }: { username: string; onLogout: () => void }) {
  return (
    <main className="login">
      <h1 className="login-title"><Logo size={44} /></h1>
      <p className="hint">Compte : {username}</p>
      <ChangePasswordForm forced />
      <p><button type="button" onClick={onLogout}>Se déconnecter</button></p>
    </main>
  );
}

export function AccountPage({ username, email, sso }: { username: string; email?: string; sso?: boolean }) {
  return (
    <>
      <PageHeader title="Mon compte" description={`Connecté en tant que ${username}${email ? ` (${email})` : ""}.`} />
      <div className="acct-grid">
        <section className="adm-card acct-id" aria-label="Mon profil">
          <span className="acct-av"><Avatar name={username} /></span>
          <h2>{username}</h2>
          {email && <p className="hint">{email}</p>}
          <p><Badge tone={sso ? "info" : "muted"}>{sso ? "Connexion SSO" : "Compte local"}</Badge></p>
        </section>
        <Section title="Sécurité" hint={sso ? undefined : "Choisissez un mot de passe que vous n'utilisez nulle part ailleurs. Vos autres sessions sont fermées au changement."}>
          {sso ? <p>Ce compte se connecte par SSO : le mot de passe se change chez votre fournisseur d'identité.</p> : <ChangePasswordForm />}
        </Section>
      </div>
    </>
  );
}
