import type { FormDefinition, FormField } from "./types";

export interface FormDiff { added: string[]; removed: string[]; changed: string[]; typeChanged: string[]; suggested: "patch" | "minor" | "major" }

const flat = (d: FormDefinition): Map<string, FormField> => {
  const out = new Map<string, FormField>();
  for (const s of d.sections ?? []) for (const f of s.fields ?? []) out.set(f.key, f);
  return out;
};
const label = (f: FormField) => (f.label ? `${f.label} (${f.key})` : f.key);

/** What a publication changes, compared with the last published version: fields added, removed, modified (and among them those whose
 *  type changed, which breaks what the workflow receives). The suggested kind of change follows: removal or type change = major,
 *  addition = minor, anything else = patch. Only fields count; layout and style do not change what is received. */
export function formDiff(before: FormDefinition | null, after: FormDefinition): FormDiff {
  const b = before ? flat(before) : new Map<string, FormField>();
  const a = flat(after);
  const added = [...a.keys()].filter((k) => !b.has(k)).map((k) => label(a.get(k) as FormField));
  const removed = [...b.keys()].filter((k) => !a.has(k)).map((k) => label(b.get(k) as FormField));
  const changed: string[] = [], typeChanged: string[] = [];
  for (const [k, f] of a) {
    const old = b.get(k);
    if (!old) continue;
    if (old.type !== f.type) typeChanged.push(label(f));
    else if (JSON.stringify({ ...old, ui: undefined, layout: undefined }) !== JSON.stringify({ ...f, ui: undefined, layout: undefined })) changed.push(label(f));
  }
  const suggested = removed.length || typeChanged.length ? "major" : added.length ? "minor" : "patch";
  return { added, removed, changed, typeChanged, suggested };
}
