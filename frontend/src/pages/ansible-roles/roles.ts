/** Rôles Ansible de la bibliothèque (ADR 0037, lot 6) : les types échangés avec l'API et les petites règles de l'éditeur, testables sans écran. */

export interface RoleVersion {
  version: string;
  digest: string;
  changelog: string;
  files: number;
  created_at: string;
}
export interface RoleVariable {
  name: string;
  type: string;
  required: boolean;
  default?: unknown;
  description: string;
}
export interface RoleOutput {
  name: string;
  description: string;
}
/** Le contrat d'usage d'un rôle : à quoi il sert et comment l'appeler. Enregistré avec la version (immuable). */
export interface RoleContractDoc {
  summary?: string;
  description?: string;
  variables?: RoleVariable[];
  outputs?: RoleOutput[];
  example?: string;
}
export const VARIABLE_TYPES: [string, string][] = [
  ["str", "Texte"],
  ["int", "Entier"],
  ["float", "Nombre"],
  ["bool", "Oui / Non"],
  ["list", "Liste"],
  ["dict", "Dictionnaire"],
  ["path", "Chemin"],
];
export interface RoleSummary {
  name: string;
  summary: string;
  contract_ok: boolean;
  variables: number;
  description: string;
  folder: string;
  latest_version: string | null;
  versions: RoleVersion[];
  used_by: number;
}
export interface RoleVersionFiles {
  version: string;
  digest: string;
  changelog: string;
  files: Record<string, string>;
  contract?: RoleContractDoc;
  contract_problems?: string[];
}

/** Le contrat tel qu'on le saisit : les champs absents valent vides, SANS rogner les textes (sinon on ne pourrait pas taper une espace en fin de mot). */
export function viewContract(c: RoleContractDoc | undefined): Required<RoleContractDoc> {
  return {
    summary: c?.summary ?? "",
    description: c?.description ?? "",
    variables: (c?.variables ?? []).map((v) => ({ ...v, description: v.description ?? "", required: !!v.required })),
    outputs: (c?.outputs ?? []).map((o) => ({ ...o, description: o.description ?? "" })),
    example: c?.example ?? "",
  };
}

/** Le contrat sous une forme canonique : ce qui n'est pas renseigné vaut vide, pour comparer deux états sans fausse différence. */
export function normContract(c: RoleContractDoc | undefined): Required<RoleContractDoc> {
  return {
    summary: (c?.summary ?? "").trim(),
    description: (c?.description ?? "").trim(),
    variables: (c?.variables ?? []).map((v) => ({ ...v, description: v.description ?? "", required: !!v.required })),
    outputs: (c?.outputs ?? []).map((o) => ({ ...o, description: o.description ?? "" })),
    example: (c?.example ?? "").trim(),
  };
}
export const sameContract = (a: RoleContractDoc | undefined, b: RoleContractDoc | undefined) =>
  JSON.stringify(normContract(a)) === JSON.stringify(normContract(b));

/** Ce qu'il manque pour qu'on comprenne le rôle : mêmes conseils que le serveur. */
export function contractAdvice(c: RoleContractDoc | undefined): string[] {
  const n = normContract(c);
  return [
    ...(n.summary ? [] : ["Dites en une phrase à quoi sert le rôle."]),
    ...n.variables.filter((v) => !v.description.trim()).map((v) => `Décrivez la variable « ${v.name} ».`),
  ];
}

/** Un exemple de playbook qui appelle le rôle : ses variables obligatoires, puis celles qui ont une valeur par défaut. */
export function exampleFor(role: string, c: RoleContractDoc | undefined): string {
  const vars = normContract(c).variables.filter((v) => v.required || v.default !== undefined);
  const lines = ["- hosts: all", "  roles:", `    - role: ${role}`];
  if (vars.length > 0) {
    lines.push("      vars:");
    for (const v of vars) lines.push(`        ${v.name}: ${v.default === undefined ? "# à renseigner" : JSON.stringify(v.default)}`);
  }
  return `${lines.join("\n")}\n`;
}

export const ROLE_NAME = /^[a-z][a-z0-9_]{0,49}$/;
/** Les dossiers d'un rôle : du YAML (`yaml`) ou du texte libre. Mêmes règles que le serveur. */
export const DIRS: { name: string; yaml: boolean; help: string }[] = [
  { name: "tasks", yaml: true, help: "Les tâches du rôle. main.yml est le point d'entrée (obligatoire)." },
  { name: "handlers", yaml: true, help: "Actions déclenchées par « notify » (redémarrer un service…)." },
  { name: "defaults", yaml: true, help: "Valeurs par défaut des variables, faciles à remplacer." },
  { name: "vars", yaml: true, help: "Variables fixes du rôle." },
  { name: "meta", yaml: true, help: "Métadonnées : « dependencies » liste les rôles dont celui-ci dépend." },
  { name: "templates", yaml: false, help: "Modèles Jinja (.j2) pour le module « template »." },
  { name: "files", yaml: false, help: "Fichiers copiés tels quels avec le module « copy »." },
];
const PART = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/;

/** Pourquoi ce chemin est refusé, ou `null`. */
export function pathProblem(path: string): string | null {
  if (!path || path.length > 200 || path.includes("\\") || path.startsWith("/")) return "Chemin invalide.";
  const parts = path.split("/");
  if (parts.length < 2) return `Rangez le fichier dans un dossier : ${DIRS.map((d) => d.name).join(", ")}.`;
  if (parts.length > 6 || !parts.every((p) => PART.test(p) && p !== "." && p !== ".."))
    return "Lettres, chiffres, point, tiret et souligné ; 6 niveaux au plus ; pas de « .. ».";
  const dir = DIRS.find((d) => d.name === parts[0]);
  if (!dir) return `Le dossier « ${parts[0]} » n'existe pas dans un rôle : ${DIRS.map((d) => d.name).join(", ")}.`;
  if (dir.yaml && !/\.ya?ml$/.test(path)) return `Les fichiers de « ${dir.name} » sont en YAML (.yml).`;
  return null;
}

/** Ce qui empêche de publier, ou `null`. */
export function filesProblem(files: Record<string, string>): string | null {
  const paths = Object.keys(files);
  if (!paths.some((p) => p === "tasks/main.yml" || p === "tasks/main.yaml")) return "Il manque tasks/main.yml : c'est le point d'entrée d'un rôle.";
  for (const p of paths) {
    const why = pathProblem(p);
    if (why) return `${p} : ${why}`;
  }
  return null;
}

/** Les fichiers rangés par dossier, dans l'ordre d'un rôle (tasks d'abord). */
export function grouped(files: Record<string, string>): { dir: string; paths: string[] }[] {
  const order = DIRS.map((d) => d.name);
  const by = new Map<string, string[]>();
  for (const p of Object.keys(files).sort()) by.set(p.split("/")[0] ?? p, [...(by.get(p.split("/")[0] ?? p) ?? []), p]);
  return [...by.entries()].sort(([a], [b]) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99)).map(([dir, paths]) => ({ dir, paths }));
}

/** La version suivante proposée : le numéro de correctif augmente. */
export function nextVersion(latest: string | null): string {
  if (!latest) return "1.0.0";
  const [a = "1", b = "0", c = "0"] = latest.split(".");
  return `${a}.${b}.${Number(c) + 1}`;
}
export const isSemver = (v: string): boolean => /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(v);
export const sameFiles = (a: Record<string, string>, b: Record<string, string>): boolean => {
  const ka = Object.keys(a);
  return ka.length === Object.keys(b).length && ka.every((k) => a[k] === b[k]);
};

/** Le contenu proposé pour un nouveau fichier selon son dossier. */
export function starter(path: string): string {
  if (path.startsWith("tasks/")) return "- name: Ma tâche\n  ansible.builtin.debug:\n    msg: bonjour\n";
  if (path.startsWith("handlers/")) return "- name: mon_handler\n  ansible.builtin.debug:\n    msg: déclenché\n";
  if (path.startsWith("defaults/") || path.startsWith("vars/")) return "ma_variable: valeur\n";
  if (path.startsWith("meta/")) return "dependencies: []\n";
  return "";
}
export const STARTER_ROLE: Record<string, string> = {
  "tasks/main.yml": "- name: Annoncer le rôle\n  ansible.builtin.debug:\n    msg: \"{{ message | default('bonjour') }}\"\n",
  "defaults/main.yml": "message: bonjour\n",
};

/** Le fichier montré à l'ouverture : tasks/main.yml, le point d'entrée du rôle, sinon le premier dans l'ordre d'affichage. */
export function firstFile(files: Record<string, string>): string | undefined {
  if ("tasks/main.yml" in files) return "tasks/main.yml";
  return grouped(files)[0]?.paths[0];
}
