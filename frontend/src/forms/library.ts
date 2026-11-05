import type { FormDefinition } from "./types";

export interface FormVersionInfo { version: string; created_at: string; changelog: string; digest: string }
export interface FormCard {
  slug: string; name: string; description: string; folder: string; tags: string[]; is_template: boolean; owner: string | null;
  latest_version: FormVersionInfo | null; draft_revision: number; fields: number; created_at: string; updated_at: string;
}
export interface FormUsage {
  workflows: { slug: string; versions: string[]; in_draft: boolean }[];
  public_forms: { id: number; name: string; workflow: string; version: string | null }[];
}
export interface FormFull extends FormCard { definition: FormDefinition; versions: FormVersionInfo[]; usage: FormUsage }
export interface BuiltinTemplate { id: string; name: string; description: string; definition: FormDefinition }

export const slugify = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 50);
