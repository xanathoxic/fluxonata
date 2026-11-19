/** Ce que le serveur dit des extensions branchées (GET /workspaces/{espace}/extensions/) : déjà filtré par les droits de la personne. */
export interface ExtensionMenuItem {
  id: string;
  label: string;
  path: string;
  group: string;
  icon: string;
}
export interface ExtensionInfo {
  id: string;
  name: string;
  version: string;
  description: string;
  /** adresse du module d'interface (ES module), ou null */
  ui: string | null;
  menu: ExtensionMenuItem[];
}
export interface ExtensionsAnswer {
  api: number;
  extensions: ExtensionInfo[];
  refused?: { name: string; reason: string }[];
}

/** Ce que l'interface prête à une extension : rien d'autre que ceci (pas d'accès au reste de l'application). */
export interface ExtensionHost {
  /** version du contrat entre l'interface et les extensions */
  apiVersion: 1;
  workspace: { slug: string; name: string };
  user: { username: string };
  /** chemin demandé à l'intérieur de l'extension (« /reglages ») */
  path: string;
  /** appelle l'API de la plateforme (cookie de session et CSRF gérés) ; le chemin commence par « / » et est relatif à `/api/v1` */
  api: <T = unknown>(path: string, init?: { method?: string; json?: unknown }) => Promise<T>;
  navigate: (to: string) => void;
}
/** Un module d'extension exporte par défaut cette fonction ; elle renvoie (ou non) la fonction qui défait ce qu'elle a monté. */
export type ExtensionMount = (el: HTMLElement, host: ExtensionHost) => (() => void) | undefined;

export const extensionPath = (ext: string, path: string): string => `/x/${ext}${path === "/" ? "" : path}`;
