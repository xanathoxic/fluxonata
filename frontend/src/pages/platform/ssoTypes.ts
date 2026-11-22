import type { ApiError } from "../../api";

export type Kind = "oidc" | "saml";
export interface Provider {
  slug: string;
  name: string;
  kind: Kind;
  enabled: boolean;
  auto_create: boolean;
  unmapped: "allow" | "deny";
  config: Record<string, unknown>;
  has_secret: boolean;
  endpoints: Record<string, string>;
  rules: number;
  identities: number;
  updated_at: string;
}
export interface Listing {
  results: Provider[];
  environment: { issuer: string; client_id: string; login: string; group_roles: boolean } | null;
  base_url: string;
  public_url_configured: boolean;
  roles: { id: string; label: string }[];
  name_id_formats: { id: string; label: string }[];
}
export interface Rule {
  group: string;
  match: "exact" | "glob";
  grant: "workspace" | "platform_admin";
  workspace: string | null;
  role: string;
  user_group: string;
}
export interface Simulation {
  groups: string[];
  matched: { rule: number; group: string; pattern: string }[];
  workspaces: { workspace: string; name: string; role: string | null; user_groups: string[] }[];
  platform_admin: boolean;
  manages_platform_admin: boolean;
  login_allowed: boolean;
  login_denied_reason: string;
}
export interface Space {
  slug: string;
  name: string;
  archived: boolean;
}

/** Les messages du serveur, par champ (la validation renvoie {champ: message}). */
export function fieldErrors(e: unknown): Record<string, string> {
  const d = (e as Partial<ApiError> | null)?.details;
  if (!d || typeof d !== "object") return {};
  return Object.fromEntries(Object.entries(d as Record<string, unknown>).map(([k, v]) => [k, Array.isArray(v) ? v.join(", ") : String(v)]));
}
export const KIND_LABEL: Record<Kind, string> = { oidc: "OpenID Connect", saml: "SAML 2.0" };
