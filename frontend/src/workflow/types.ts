import type { JsonSchema } from "../api";

export type NodeType = "action" | "condition" | "switch" | "join" | "race" | "wait" | "approval" | "subworkflow" | "foreach" | "ai" | "script" | "throw" | "http" | "sql" | "agent" | "while" | "ldap" | "sftp" | "smtp" | "ssh" | "transform" | "cache" | "schedule" | "file" | "model";
export type ScriptLanguage = "python" | "javascript";
export type VarType = "string" | "integer" | "number" | "boolean" | "array" | "object";
export interface ScriptOutput { name: string; type: VarType }
// "next", "true", "false", "error", or the id of a case of a multi-way choice ("default" included)
export type EdgeLabel = string;
export interface SwitchCase { id: string; expr: string }

/** The server accepts a pattern as a text or as a list, and a command as a text or as an object: the editor reads both. */
export interface SshCommand { id?: string; run: string; sensitive?: boolean; ignore_error?: boolean; expect_exit?: number[]; timeout_seconds?: number }
export interface SshResponse { expect: string; send?: string; send_secret?: string; eol?: string; max?: number; sensitive?: boolean }
export interface SshStep {
  id?: string; send?: string; send_secret?: string; eol?: string; expect?: string | string[]; success?: string | string[]; failure?: string | string[];
  responses?: SshResponse[]; sensitive?: boolean; ignore_error?: boolean; timeout_seconds?: number;
}
/** A jump written with its own host (its connection is a reusable identity); a jump whose host comes from the connection is a plain name. */
export interface SshJump { connection: string; host?: string; port?: number | string; host_key_sha256?: string }
export interface SshPty { enabled?: boolean; term?: string; cols?: number; rows?: number }

export interface GraphNode {
  id: string;
  type: NodeType;
  name: string;
  ui?: { x: number; y: number };
  action?: string;
  version?: string;
  inputs?: Record<string, unknown>;
  retry?: { max_attempts: number; backoff_seconds: number; jitter: number };
  compensation?: { action: string; inputs: Record<string, unknown> };
  expr?: string;
  cases?: SwitchCase[];
  ai_review?: boolean; // script written by the AI: blocks publication until a person confirms having read it
  seconds?: number;
  when?: "at" | "in"; // « Programmer » : à une date et une heure, ou après un délai
  at?: string; // « Programmer » : 2026-12-01T08:30 (heure du fuseau du nœud) ou une expression
  format?: "text" | "csv" | "xlsx" | "pdf" | "docx" | "pptx" | "html" | "zip"; // « Créer un fichier »
  filename?: string;
  markdown?: string; // « Modèle de document »
  model?: string; // « Créer un fichier » : expression du modèle utilisé (ex. {{ steps.modele.output }})
  body_model?: string; // « Envoyer un e-mail » : idem
  rows?: string;
  columns?: string[];
  sheet?: string;
  sources?: string[];
  until?: string; // « Attente » : reprise à une date et une heure (2026-12-01T08:30) ou expression ; absent = attente d'une durée (`seconds`)
  timezone?: string;
  on_past?: "fail" | "now";
  connection?: string;
  jumps?: (string | SshJump)[]; // SSH: jump connections, in order (a name, or an object when the step names the host)
  host?: string; // SSH: host of the target when its connection has none (a reusable identity)
  port?: number | string;
  host_key_sha256?: string; // SSH: fingerprint of that host's server key (mandatory with `host`)
  session?: "exec" | "shell";
  commands?: SshCommand[] | string; // SSH exec: a list, or an expression that produces one
  steps?: SshStep[]; // SSH shell
  pty?: SshPty;
  stop_on_error?: boolean;
  command_timeout_seconds?: number;
  max_output_bytes?: number;
  connect_retries?: number;
  secrets?: Record<string, string>; // SSH: alias -> name of a secret of the workspace (never a value)
  mocks?: unknown[];
  integration?: string;
  operation?: string;
  /** Cache step: key, value to write, lifetime, maximum age accepted when reading, behaviour when missing. */
  key?: string;
  value?: unknown;
  ttl_seconds?: number | string;
  max_age_seconds?: number | string;
  on_missing?: "empty" | "fail" | "rebuild";
  rebuild_inputs?: Record<string, unknown> | string;
  sensitive?: boolean;
  content?: unknown;
  to?: string;
  cc?: string;
  subject?: string;
  params?: Record<string, unknown>;
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path?: string;
  query?: Record<string, unknown>;
  headers?: Record<string, unknown>;
  body?: unknown;
  mode?: "test" | "reel";
  fail_on_status?: boolean;
  body_format?: "json" | "form" | "multipart" | "file";
  files?: { field: string; filename?: string; content?: string; file?: unknown; content_type?: string }[];
  file?: unknown;
  response_file?: boolean | { name?: string };
  binary?: boolean;
  losers?: "cancel" | "finish";
  goal?: string;
  filter?: string;
  attributes?: string[];
  base?: string;
  scope?: "sub" | "one" | "base";
  condition?: string;
  delay_seconds?: number;
  interval_seconds?: number;
  tools?: string[];
  max_steps?: number;
  approval?: "never" | "effectful" | "always";
  statement?: string;
  max_rows?: number;
  op?: "set" | "map" | "filter" | "sort" | "group" | "unique" | "merge" | "limit" | "split" | "chunk";
  text?: string;
  separator?: string;
  size?: number;
  where?: string;
  by?: string;
  order?: "asc" | "desc";
  right?: string;
  merge_mode?: "inner" | "left" | "append";
  left_by?: string;
  right_by?: string;
  count?: number;
  skip?: number;
  fields?: Record<string, unknown>;
  aggregates?: { name: string; fn: string; expr?: string }[];
  pagination?: { type: "page" | "offset" | "cursor" | "link"; items_path?: string; max_pages?: number; page_size?: number; page_param?: string; offset_param?: string; cursor_param?: string; size_param?: string; next_path?: string };
  resume?: "timer" | "webhook" | "form";
  form?: { properties: Record<string, { type: string; title?: string }>; required: string[] } | null;
  title?: string;
  instructions?: string;
  allow_self_approval?: boolean;
  workflow?: string;
  items?: string;
  item_input?: string;
  max_iterations?: number;
  concurrency?: number;
  provider?: string;
  prompt?: string;
  skill?: string; // AI step using a library skill (instead of a free-form prompt)
  skill_version?: string; // pinned at publication
  output_schema?: JsonSchema;
  connections?: Record<string, string>;
  description?: string;
  language?: ScriptLanguage;
  script?: string;
  bindings?: Record<string, unknown>;
  outputs?: ScriptOutput[];
  timeout_seconds?: number;
  code?: string;
  message?: string;
}

export interface GraphEdge {
  from: string;
  to: string;
  label?: EdgeLabel;
  /** Tracé choisi par la personne : le lien est toujours orthogonal ; `route` en donne les coordonnées intérieures (abscisse du premier segment
   *  vertical, ordonnée du segment horizontal suivant, etc. : voir `edgeRoute.ts`), absentes tant que le tracé est automatique. `cx` est l'ancien
   *  réglage d'un seul segment vertical (lu comme `route: [cx]`) ; `points` l'ancien tracé libre, qui n'est plus dessiné. Les deux disparaissent
   *  au premier réglage. `sf` et `tf` fixent la face de départ et d'arrivée (haut, droite, bas, gauche) quand la personne les a choisies en reliant les
   *  étapes ; absentes, les faces sont choisies automatiquement et suivent les étapes quand on les déplace. */
  ui?: { route?: number[]; sf?: "t" | "r" | "b" | "l"; tf?: "t" | "r" | "b" | "l"; cx?: number; points?: { x: number; y: number }[] };
}

/** Une variable du workflow peut aussi porter n'importe quel JSON ; jamais une entrée ni une sortie de script. */
export type VariableType = VarType | "json";
export interface Variable { name: string; type: VariableType; value: unknown; description?: string; env_values?: Partial<Record<"dev" | "test" | "prod", unknown>> }
export interface FormStep { title: string; description?: string; fields: string[] }
export interface FormDef { steps: FormStep[] }
/** Une note du schéma (annotation visuelle, jamais exécutée) : sa position, sa taille (absente : automatique), sa couleur (#rrggbb) et l'opacité de son fond (0,1 à 1). */
export interface Note { id: string; text: string; ui: { x: number; y: number; w?: number; h?: number }; color?: string; opacity?: number }

/** Un point de jonction : l'endroit où les liens qui partagent un point de connexion d'une étape (`node`, `face`) se séparent (`role: "out"`, ils partent du même point) ou
 *  se rejoignent (`role: "in"`, ils arrivent au même point). Il se déplace comme une étape, `x` et `y` étant son centre, et tous ses liens le suivent ; `flip` l'inverse (double-clic : l'entrée verte passe à droite, les sorties rouges à gauche). Purement visuel :
 *  le workflow lui-même n'en sait rien (les liens vont toujours d'une étape à une autre). */
export interface Junction { id: string; node: string; role: "in" | "out"; face: "t" | "r" | "b" | "l"; x: number; y: number; flip?: boolean }

export interface Graph {
  junctions?: Junction[];
  variables?: Variable[];
  form?: FormDef | null;
  /** designed input form (sections, rules, dynamic lists); takes precedence over `form` */
  input_form?: import("../forms/types").InputForm | null;
  notes?: Note[];
  pins?: Record<string, Record<string, unknown>>;
  /** where the person put the virtual « Début » and « Fin » nodes (computed from the steps when absent) */
  layout?: { start?: { x: number; y: number }; end?: { x: number; y: number } };
  inputs: JsonSchema;
  nodes: GraphNode[];
  edges: GraphEdge[];
  outputs: Record<string, string>;
  meta?: { ai_generated?: boolean; provider?: string };
}

export interface Issue {
  node: string | null;
  level: "error" | "warning";
  code: string;
  message: string;
}

export const NODE_LABELS: Record<NodeType, string> = {
  action: "Élément action",
  condition: "Décision",
  switch: "Choix multiple",
  join: "Jonction",
  race: "Premier arrivé",
  wait: "Minuterie d'attente",
  approval: "Interaction utilisateur",
  subworkflow: "Élément workflow",
  foreach: "Pour chaque",
  ai: "Étape IA",
  script: "Tâche script",
  throw: "Exception",
  http: "Requête HTTP",
  sql: "Requête SQL",
  agent: "Agent IA",
  while: "Répéter tant que",
  ldap: "Annuaire LDAP",
  sftp: "Fichier SFTP",
  ssh: "Commandes SSH",
  smtp: "Envoyer un e-mail",
  transform: "Transformer les données",
  cache: "Cache",
  schedule: "Programmer un lancement",
  file: "Créer un fichier",
  model: "Modèle de document",
};

const FIXED_LABELS: Record<string, string> = { next: "Ensuite", true: "Si oui", false: "Si non", error: "En cas d'erreur", default: "Sinon" };
export const edgeText = (label: string): string => FIXED_LABELS[label] ?? `Cas « ${label} »`;
