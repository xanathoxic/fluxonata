import type { JsonSchema } from "../api";

export type NodeType = "action" | "condition" | "join" | "wait" | "approval" | "subworkflow" | "foreach" | "ai" | "script" | "throw";
export type ScriptLanguage = "python" | "javascript";
export type VarType = "string" | "integer" | "number" | "boolean" | "array" | "object";
export interface ScriptOutput { name: string; type: VarType }
export type EdgeLabel = "next" | "true" | "false" | "error";

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
  ai_review?: boolean; // script written by the AI: blocks publication until a person confirms having read it
  seconds?: number;
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
}

export interface Variable { name: string; type: VarType; value: unknown; description?: string }
export interface FormStep { title: string; description?: string; fields: string[] }
export interface FormDef { steps: FormStep[] }
export interface Note { id: string; text: string; ui: { x: number; y: number } }

export interface Graph {
  variables?: Variable[];
  form?: FormDef | null;
  notes?: Note[];
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
  join: "Jonction",
  wait: "Minuterie d'attente",
  approval: "Interaction utilisateur",
  subworkflow: "Élément workflow",
  foreach: "Pour chaque",
  ai: "Étape IA",
  script: "Tâche script",
  throw: "Exception",
};

export const EDGE_LABELS: Record<EdgeLabel, string> = { next: "Ensuite", true: "Si oui", false: "Si non", error: "En cas d'erreur" };
