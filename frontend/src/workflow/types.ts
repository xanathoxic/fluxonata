import type { JsonSchema } from "../api";

export type NodeType = "action" | "condition" | "switch" | "join" | "race" | "wait" | "approval" | "subworkflow" | "foreach" | "ai" | "script" | "throw" | "http" | "sql" | "agent" | "while" | "ldap" | "sftp" | "smtp" | "transform";
export type ScriptLanguage = "python" | "javascript";
export type VarType = "string" | "integer" | "number" | "boolean" | "array" | "object";
export interface ScriptOutput { name: string; type: VarType }
// "next", "true", "false", "error", or the id of a case of a multi-way choice ("default" included)
export type EdgeLabel = string;
export interface SwitchCase { id: string; expr: string }

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
  connection?: string;
  integration?: string;
  operation?: string;
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
  /** routing chosen by the person: x of the vertical segment (the line is drawn automatically when absent) */
  ui?: { cx: number };
}

export interface Variable { name: string; type: VarType; value: unknown; description?: string; env_values?: Partial<Record<"dev" | "test" | "prod", unknown>> }
export interface FormStep { title: string; description?: string; fields: string[] }
export interface FormDef { steps: FormStep[] }
export interface Note { id: string; text: string; ui: { x: number; y: number } }

export interface Graph {
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
  smtp: "Envoyer un e-mail",
  transform: "Transformer les données",
};

const FIXED_LABELS: Record<string, string> = { next: "Ensuite", true: "Si oui", false: "Si non", error: "En cas d'erreur", default: "Sinon" };
export const edgeText = (label: string): string => FIXED_LABELS[label] ?? `Cas « ${label} »`;
