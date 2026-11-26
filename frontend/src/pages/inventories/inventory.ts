/** Inventaires Ansible (ADR 0037, lot 3) : le document échangé avec l'API et les petites fonctions de l'éditeur, testables sans écran. */

export interface InvGroup {
  name: string;
  variables: Record<string, unknown>;
  children: string[];
}
export interface InvHost {
  name: string;
  variables: Record<string, unknown>;
  enabled: boolean;
  groups: string[];
}
export interface InvDoc {
  slug: string;
  name: string;
  description: string;
  variables: Record<string, unknown>;
  groups: InvGroup[];
  hosts: InvHost[];
}
export interface InvSummary {
  slug: string;
  name: string;
  description: string;
  folder: string;
  hosts: number;
  groups: number;
  updated_at: string;
}

/** Même règle que le serveur : pas de tiret dans un nom de groupe (Ansible le refuse). */
export const GROUP_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
export const HOST_NAME = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$/;

/** Les variables s'écrivent en JSON dans l'éditeur : un objet, ou rien. */
export function parseVars(text: string): { ok: true; value: Record<string, unknown> } | { ok: false; message: string } {
  if (!text.trim()) return { ok: true, value: {} };
  try {
    const v: unknown = JSON.parse(text);
    if (v === null || typeof v !== "object" || Array.isArray(v))
      return { ok: false, message: 'Un objet JSON est attendu, par exemple {"ansible_user": "deploy"}.' };
    return { ok: true, value: v as Record<string, unknown> };
  } catch {
    return { ok: false, message: "JSON invalide." };
  }
}
export const varsText = (v: Record<string, unknown>): string => (Object.keys(v).length ? JSON.stringify(v) : "");

/** Ce qui empêche d'enregistrer, dans l'ordre où la personne le rencontre ; `null` quand tout est bon. */
export function docProblem(doc: InvDoc): string | null {
  if (!doc.name.trim()) return "Donnez un nom à l'inventaire.";
  const seen = new Set<string>();
  for (const g of doc.groups) {
    if (!GROUP_NAME.test(g.name)) return `Groupe « ${g.name || "(sans nom)"} » : lettres, chiffres et soulignés seulement, sans tiret ni espace.`;
    if (["all", "ungrouped"].includes(g.name)) return `« ${g.name} » est réservé par Ansible.`;
    if (seen.has(g.name)) return `Le groupe « ${g.name} » existe deux fois.`;
    seen.add(g.name);
  }
  const hosts = new Set<string>();
  for (const h of doc.hosts) {
    if (!HOST_NAME.test(h.name)) return `Machine « ${h.name || "(sans nom)"} » : nom invalide.`;
    if (hosts.has(h.name)) return `La machine « ${h.name} » existe deux fois.`;
    hosts.add(h.name);
  }
  return loopAround(doc.groups);
}

/** Un groupe ne peut pas, de proche en proche, se contenir lui-même. */
export function loopAround(groups: InvGroup[]): string | null {
  const kids = new Map(groups.map((g) => [g.name, g.children]));
  const state = new Map<string, number>();
  let hit: string | null = null;
  const visit = (n: string): void => {
    if (hit) return;
    if (state.get(n) === 1) {
      hit = n;
      return;
    }
    if (state.get(n) === 2) return;
    state.set(n, 1);
    for (const c of kids.get(n) ?? []) visit(c);
    state.set(n, 2);
  };
  for (const g of groups) visit(g.name);
  return hit ? `Les groupes forment une boucle (autour de « ${hit} »).` : null;
}

/** Renommer un groupe le renomme aussi chez ses parents et dans les machines qui en font partie. */
export function renameGroup(doc: InvDoc, from: string, to: string): InvDoc {
  const swap = (l: string[]) => l.map((n) => (n === from ? to : n));
  return {
    ...doc,
    groups: doc.groups.map((g) => ({ ...g, name: g.name === from ? to : g.name, children: swap(g.children) })),
    hosts: doc.hosts.map((h) => ({ ...h, groups: swap(h.groups) })),
  };
}
/** Supprimer un groupe le retire des parents et des machines (les machines elles-mêmes restent). */
export function removeGroup(doc: InvDoc, name: string): InvDoc {
  const drop = (l: string[]) => l.filter((n) => n !== name);
  return {
    ...doc,
    groups: doc.groups.filter((g) => g.name !== name).map((g) => ({ ...g, children: drop(g.children) })),
    hosts: doc.hosts.map((h) => ({ ...h, groups: drop(h.groups) })),
  };
}
export const toggleIn = (list: string[], name: string, on: boolean): string[] => (on ? [...new Set([...list, name])] : list.filter((n) => n !== name));
