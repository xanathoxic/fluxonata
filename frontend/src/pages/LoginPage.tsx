import { Logo } from "../components/Logo";
import { useEffect, useState, type FormEvent } from "react";
import { api, type ApiError, type Me } from "../api";
import { t } from "../i18n";

export function LoginPage({ onLoggedIn }: { onLoggedIn: (me: Me) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sso, setSso] = useState<string | null>(null);
  useEffect(() => {
    api<{ enabled: boolean; login_url: string | null }>("/auth/oidc/config/").then((c) => setSso(c.enabled ? c.login_url : null)).catch(() => setSso(null));
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await fetch("/api/v1/auth/csrf/", { credentials: "same-origin" });
      onLoggedIn(await api<Me>("/auth/login/", { json: { username, password } }));
    } catch (err) {
      const status = (err as Partial<ApiError>).status;
      setError(status === 401 ? t("login.error") : status === 429 ? t("login.throttled") : t("login.unavailable"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login">
      <h1 className="login-title"><Logo size={44} /></h1>
      <form onSubmit={submit} aria-labelledby="login-title">
        <h2 id="login-title">{t("login.title")}</h2>
        <div className="field">
          <label htmlFor="u">{t("login.username")}</label>
          <input id="u" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor="p">{t("login.password")}</label>
          <input id="p" type="password" autoComplete="current-password" value={password}
            onChange={(e) => setPassword(e.target.value)} required aria-describedby={error ? "login-err" : undefined} />
        </div>
        {error && <p id="login-err" role="alert" className="field-error">Erreur : {error}</p>}
        <button type="submit" className="primary" disabled={busy}>{t("login.submit")}</button>
      </form>
      {sso && <p><a className="button" href={sso}>Se connecter avec le SSO de l'organisation</a></p>}
    </main>
  );
}
