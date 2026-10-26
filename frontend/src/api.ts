export interface ApiError {
  status: number;
  code: string;
  message: string;
  details: Record<string, unknown> | null;
}

function cookie(name: string): string {
  return document.cookie.split("; ").find((c) => c.startsWith(`${name}=`))?.split("=")[1] ?? "";
}

export async function api<T>(path: string, init: Omit<RequestInit, "headers" | "body"> & { json?: unknown; form?: FormData; headers?: Record<string, string> } = {}): Promise<T> {
  const method = init.method ?? (init.json !== undefined || init.form ? "POST" : "GET");
  const headers: Record<string, string> = { Accept: "application/json", ...init.headers };
  if (init.json !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    if (!cookie("csrftoken")) await fetch("/api/v1/auth/csrf/", { credentials: "same-origin" });
    headers["X-CSRFToken"] = cookie("csrftoken");
  }
  const res = await fetch(`/api/v1${path}`, {
    ...init,
    method,
    headers,
    credentials: "same-origin",
    body: init.form ?? (init.json !== undefined ? JSON.stringify(init.json) : undefined),
  });
  if (res.status === 204) return undefined as T;
  const body = (await res.json().catch(() => ({}))) as { error?: { code: string; message: string; details: Record<string, unknown> | null } };
  if (!res.ok) {
    const e: ApiError = {
      status: res.status,
      code: body.error?.code ?? "error",
      message: body.error?.message ?? "Erreur",
      details: body.error?.details ?? null,
    };
    throw e;
  }
  return body as T;
}

export interface Workspace {
  slug: string;
  name: string;
  role: string;
  permissions: string[];
  environments: { kind: string; name: string }[];
}
export interface Me {
  username: string;
  email?: string;
  must_change_password?: boolean;
  sso_only?: boolean;
  is_platform_admin: boolean;
  workspaces: Workspace[];
}
export interface JsonSchema {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  examples?: unknown[];
  enum?: unknown[];
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  format?: string;
  writeOnly?: boolean;
  required?: string[];
  properties?: Record<string, JsonSchema>;
}
export interface ActionVersion {
  version: string;
  input_schema: JsonSchema;
  output_schema: JsonSchema;
  license: string;
  side_effects: string;
  idempotent: boolean;
  examples: Record<string, unknown>[];
}
export interface Action {
  slug: string;
  name: string;
  description: string;
  domain: string;
  folder: string;
  tags: string[];
  owner: string | null;
  status: string;
  deprecation_reason?: string;
  latest_version: ActionVersion | null;
}
export interface Execution {
  id: string;
  action: { slug: string; name: string; version: string; output_schema: JsonSchema };
  environment: string;
  requested_by: string | null;
  status: "queued" | "running" | "succeeded" | "failed";
  status_label: string;
  inputs: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: { code: string; message: string; next_step?: string } | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  attempt_count: number;
  runner_name?: string | null;
  events?: { seq: number; type: string; message: string; created_at: string }[];
  attempts?: { number: number; status: string; logs: string }[];
}
export interface Page<T> {
  results: T[];
  count?: number;
  next?: string | null;
}

export interface WorkflowSummary {
  slug: string;
  name: string;
  folder: string;
  description: string;
  owner: string | null;
  deprecation_reason?: string;
  draft_revision: number;
  updated_at: string;
  deprecated: boolean;
  in_catalog?: boolean;
  latest_version: { version: string; created_at: string; changelog: string; input_schema: JsonSchema; form?: import("./workflow/types").FormDef | null } | null;
}
export interface WorkflowDetail extends WorkflowSummary {
  draft_graph: import("./workflow/types").Graph;
}
export interface StepRun {
  node_id: string;
  name: string;
  node_type: string;
  status: string;
  status_label: string;
  attempt: number;
  output: Record<string, unknown> | null;
  error: { code: string; message: string; next_step?: string } | null;
  available_at: string | null;
  started_at: string;
  finished_at: string | null;
  approval_id: number | null;
  execution_id: string | null;
  child_run_ids: string[];
  logs?: string;
}
export interface RunEvent {
  seq: number;
  type: string;
  node_id: string;
  message: string;
  created_at: string;
}
export interface WorkflowRun {
  id: string;
  workflow: { slug: string; version: string; name: string };
  environment: string;
  requested_by: string | null;
  status: "running" | "compensating" | "succeeded" | "failed" | "cancelled";
  status_label: string;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown> | null;
  error: { code: string; message: string; next_step?: string; failed_step?: string; compensation?: { step: string; status: string }[] } | null;
  created_at: string;
  finished_at: string | null;
  needs_decision: boolean;
  cancel_requested: boolean;
  steps?: StepRun[];
  events?: RunEvent[];
  graph?: { nodes: { id: string; name: string; type: string; ui: { x: number; y: number } | null }[]; edges: { from: string; to: string; label?: string }[]; notes?: { id: string; text: string; ui: { x: number; y: number } }[] };
  runtime?: { image?: string; digest?: string | null };
}
export interface Approval {
  id: number;
  title: string;
  instructions: string;
  status: "pending" | "approved" | "rejected";
  requested_by: string | null;
  decided_by: string | null;
  comment: string;
  created_at: string;
  decided_at: string | null;
  run_id: string;
  workflow: string;
  context: Record<string, unknown>;
  can_decide: boolean;
}

/** Every page of a paginated list (the API pages by 50): screens that show a whole list must not silently truncate it. */
export async function apiAll<T>(path: string, cap = 2000): Promise<Page<T>> {
  const results: T[] = [];
  let offset = 0;
  for (;;) {
    const sep = path.includes("?") ? "&" : "?";
    const page = await api<Page<T>>(`${path}${sep}limit=100&offset=${offset}`);
    results.push(...page.results);
    offset += page.results.length;
    if (!page.next || page.results.length === 0 || results.length >= cap) return { results, count: page.count ?? results.length, next: null };
  }
}

export interface AiSkillVersion {
  version: string;
  instructions: string;
  input_schema: JsonSchema;
  output_schema: JsonSchema;
  default_provider: string;
  changelog: string;
  digest: string;
  created_at: string;
}
export interface AiSkill {
  slug: string;
  name: string;
  description: string;
  folder: string;
  owner: string | null;
  status: "active" | "deprecated";
  deprecation_reason: string;
  latest_version: AiSkillVersion | null;
  versions?: AiSkillVersion[];
}
