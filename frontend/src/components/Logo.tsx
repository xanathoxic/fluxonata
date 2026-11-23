import { useId } from "react";
import { topbarIsDark, useBranding } from "../branding/store";

/** Fluxonata mark: three flows (teal, indigo, violet) converging into one. Decorative: the name is always written next to it. */
export function LogoMark({ size = 30 }: { size?: number }) {
  const id = useId();
  return (
    <svg className="logo-mark" width={size * 1.27} height={size} viewBox="0 0 330 260" aria-hidden="true" focusable="false" fill="none" strokeWidth="56" strokeLinecap="round" strokeLinejoin="round">
      <defs>
        <linearGradient id={`${id}t`} x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#2dd4bf" /><stop offset="1" stopColor="#3b82f6" /></linearGradient>
        <linearGradient id={`${id}b`} x1="0" y1="1" x2="1" y2="0"><stop offset="0" stopColor="#a855f7" /><stop offset="1" stopColor="#5b5bf0" /></linearGradient>
      </defs>
      <path d="M73 30H140L232 122H296" stroke={`url(#${id}t)`} />
      <path d="M28 128H140" stroke="#3b32f0" />
      <path d="M73 230H140L232 154H296" stroke={`url(#${id}b)`} />
    </svg>
  );
}

/** Marque de la plate-forme (ADR 0033) : le logo téléversé s'il y en a un, sinon la marque d'origine, sinon une pastille à l'initiale du nom choisi. */
export function Logo({ size = 30, onTopbar = false }: { size?: number; onTopbar?: boolean }) {
  const b = useBranding();
  const picked = onTopbar && topbarIsDark(b) && b.logo_dark ? b.logo_dark : b.logo; // sur une barre sombre : le logo prévu pour les fonds sombres, s'il existe
  if (picked) {
    const showName = !b.logo_has_name;
    return (
      <span className="logo">
        <img className="logo-img" src={picked.url} alt={showName ? "" : b.name} height={size} style={{ height: size }} />
        {showName && <span className="logo-name">{b.name}</span>}
      </span>
    );
  }
  if (!b.custom_name) return <span className="logo"><LogoMark size={size} /><span className="logo-name">Flux<b>onata</b></span></span>;
  return (
    <span className="logo">
      <span className="logo-mono" aria-hidden="true" style={{ width: size, height: size, fontSize: size * 0.55 }}>{[...b.name][0]?.toUpperCase()}</span>
      <span className="logo-name">{b.name}</span>
    </span>
  );
}
