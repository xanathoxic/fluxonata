import { useEffect, useRef } from "react";
import { Link, useLocation } from "react-router-dom";
import { HelpTip } from "../components/HelpTip";

/** 404 screen. Also used when a workflow, run or action does not exist (or is not visible to this user: the two cases are deliberately indistinguishable). */
export function NotFoundPage({ title = "Page introuvable", message }: { title?: string; message?: string }) {
  const { pathname } = useLocation();
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);
  return (
    <section className="notfound" aria-labelledby="nf-title">
      <svg className="nf-art" width="220" height="150" viewBox="0 0 220 150" fill="none" aria-hidden="true" focusable="false" strokeWidth="14" strokeLinecap="round" strokeLinejoin="round">
        <defs>
          <linearGradient id="nf-t" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#2dd4bf" /><stop offset="1" stopColor="#3b82f6" /></linearGradient>
          <linearGradient id="nf-b" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stopColor="#a855f7" /><stop offset="1" stopColor="#5b5bf0" /></linearGradient>
        </defs>
        <path d="M20 22H70L112 64" stroke="url(#nf-t)" />
        <path d="M14 76H70" stroke="#3b32f0" />
        <path d="M20 130H70L112 88" stroke="url(#nf-b)" />
        <path d="M132 58l36 36M168 58l-36 36" stroke="#94a3b8" strokeDasharray="1 22" />
        <circle cx="186" cy="76" r="9" stroke="#cbd5e1" strokeWidth="6" strokeDasharray="4 10" />
      </svg>
      <p className="nf-code" aria-hidden="true">404</p>
      <h1 id="nf-title" ref={heading} tabIndex={-1}>{title}</h1>
      <p className="nf-msg">{message ?? <>La page <code>{pathname}</code> n'existe pas ou a été déplacée.</>}</p>
      <div className="actions nf-actions">
        <Link className="button primary" to="/home">Retour à l'accueil</Link>
        <Link className="button" to="/workflows">Parcourir les workflows</Link>
        <Link className="button" to="/catalog">Ouvrir le catalogue</Link>
      </div>
      <p className="help-row"><HelpTip caption="À savoir">Si vous pensiez trouver quelque chose ici, vous n'avez peut-être pas le droit de le voir : demandez l'accès à l'administrateur de votre espace.</HelpTip></p>
    </section>
  );
}
