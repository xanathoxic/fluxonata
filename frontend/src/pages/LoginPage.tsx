import { Logo } from "../components/Logo";
import { useEffect, useState, type FormEvent } from "react";
import { api, type ApiError, type Me } from "../api";
import { t } from "../i18n";

export function LoginPage({ onLoggedIn }: { onLoggedIn: (me: Me) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sso, setSso] = useState<{ slug: string; name: string; kind: string; login_url: string }[]>([]);
  // un échec de connexion SSO revient ici avec un message (?sso_error=) ; on l'affiche puis on le retire de l'adresse
  const [ssoError] = useState(() => new URLSearchParams(window.location.search).get("sso_error"));
  useEffect(() => {
    if (ssoError) window.history.replaceState(null, "", window.location.pathname);
    api<{ results: { slug: string; name: string; kind: string; login_url: string }[] }>("/auth/sso/providers/").then((c) => setSso(c.results)).catch(() => setSso([]));
  }, [ssoError]);

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
      {ssoError && <p role="alert" className="field-error login-sso-error">Connexion SSO impossible : {ssoError}</p>}
      {sso.length > 0 && (
        <nav className="login-sso" aria-label="Connexion avec un compte d'entreprise">
          {sso.map((p) => <a key={p.slug} className="button" href={p.login_url}>Se connecter avec {p.name}</a>)}
        </nav>
      )}
    </main>
  );
}
