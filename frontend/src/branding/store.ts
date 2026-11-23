import { useSyncExternalStore } from "react";
import { DEFAULT_ACCENT, isHex, navTheme, navVars, themeVars } from "./palette";

export interface AssetRef {
  url: string;
  content_type: string;
  width: number | null;
  height: number | null;
  bytes?: number | null;
}
export interface Branding {
  version: number;
  name: string;
  custom_name: boolean;
  tagline: string;
  login_message: string;
  footer: { text: string; url: string };
  accent: string;
  custom_accent: boolean;
  topbar_bg: string;
  sidebar_bg: string;
  login_style: "plain" | "tint" | "gradient";
  logo_has_name: boolean;
  logo: AssetRef | null;
  logo_dark: AssetRef | null;
  icon: AssetRef | null;
}

export const DEFAULT_BRANDING: Branding = {
  version: 0,
  name: "Fluxonata",
  custom_name: false,
  tagline: "",
  login_message: "",
  footer: { text: "", url: "" },
  accent: DEFAULT_ACCENT,
  custom_accent: false,
  topbar_bg: "",
  sidebar_bg: "",
  login_style: "plain",
  logo_has_name: true,
  logo: null,
  logo_dark: null,
  icon: null,
};

const CACHE_KEY = "fluxonata.branding";
const FAVICON = "/favicon.svg";
let state: Branding = DEFAULT_BRANDING;
let applied: string[] = [];
const listeners = new Set<() => void>();

/** Dernière image de marque connue : appliquée avant le premier affichage, pour qu'une couleur ou un logo personnalisés ne « clignotent » pas. */
function cached(): Branding | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const b = JSON.parse(raw) as Partial<Branding>;
    return typeof b.name === "string" && typeof b.accent === "string" ? { ...DEFAULT_BRANDING, ...b } : null;
  } catch {
    return null;
  }
}

function apply(b: Branding): void {
  const root = document.documentElement;
  for (const k of applied) root.style.removeProperty(k);
  const vars: Record<string, string> = b.custom_accent ? themeVars(b.accent) : {};
  // un fond de barre propre à la marque : les variables du fond et de tout ce qui s'y pose, et un repère (data-topbar, data-sidebar) que le CSS lit
  for (const [attr, prefix, bg] of [
    ["data-topbar", "tb", b.topbar_bg],
    ["data-sidebar", "sb", b.sidebar_bg],
  ] as const) {
    if (isHex(bg) && bg.toLowerCase() !== "#ffffff") {
      Object.assign(vars, navVars(prefix, bg));
      root.setAttribute(attr, "");
    } else root.removeAttribute(attr);
  }
  for (const [k, v] of Object.entries(vars)) root.style.setProperty(k, v);
  applied = Object.keys(vars);
  const icon = b.icon ?? b.logo;
  let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (!link) {
    link = document.createElement("link");
    link.rel = "icon";
    document.head.appendChild(link);
  }
  const href = icon ? icon.url : FAVICON;
  if (link.getAttribute("href") !== href) {
    link.setAttribute("href", href);
    link.removeAttribute("type");
    if (!icon) link.type = "image/svg+xml";
  }
  let meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (!meta) {
    meta = document.createElement("meta");
    meta.name = "theme-color";
    document.head.appendChild(meta);
  }
  meta.content = b.accent;
}

/** Ne garde que la partie publique d'une réponse d'administration (réglages internes, limites… n'ont rien à faire dans le cache du navigateur). */
export function toPublic(b: Branding): Branding {
  const {
    version,
    name,
    custom_name,
    tagline,
    login_message,
    footer,
    accent,
    custom_accent,
    topbar_bg,
    sidebar_bg,
    login_style,
    logo_has_name,
    logo,
    logo_dark,
    icon,
  } = b;
  return {
    version,
    name,
    custom_name,
    tagline,
    login_message,
    footer,
    accent,
    custom_accent,
    topbar_bg,
    sidebar_bg,
    login_style,
    logo_has_name,
    logo,
    logo_dark,
    icon,
  };
}

export function getBranding(): Branding {
  return state;
}

export function setBranding(b: Branding, persist = true): void {
  state = b;
  apply(b);
  if (persist) {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify(b));
    } catch {
      /* stockage indisponible : sans conséquence */
    }
  }
  for (const l of listeners) l();
}

/** À appeler avant le premier affichage. */
export function initBranding(): void {
  const c = cached();
  if (c) setBranding(c, false);
}

/** Lit l'image de marque du serveur (route publique, sans session). Une erreur laisse l'image connue en place. */
export async function loadBranding(): Promise<void> {
  try {
    const res = await fetch("/api/v1/branding/", { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (!res.ok) return;
    const b = (await res.json()) as Branding;
    if (b.version !== state.version || JSON.stringify(b) !== JSON.stringify(state)) setBranding({ ...DEFAULT_BRANDING, ...b });
  } catch {
    /* hors ligne : l'image connue reste */
  }
}

/** La barre du haut a-t-elle un fond sombre ? (le logo « pour fond sombre » s'y affiche alors) */
export const topbarIsDark = (b: Branding): boolean => isHex(b.topbar_bg) && navTheme(b.topbar_bg).dark;

export function useBranding(): Branding {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => {
        listeners.delete(cb);
      };
    },
    getBranding,
    getBranding,
  );
}
