import { useId } from "react";

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

export function Logo({ size = 30 }: { size?: number }) {
  return <span className="logo"><LogoMark size={size} /><span className="logo-name">Flux<b>onata</b></span></span>;
}
