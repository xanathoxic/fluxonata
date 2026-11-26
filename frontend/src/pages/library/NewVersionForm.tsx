import { useState } from "react";
import type { ApiError } from "../../api";
import { errorText, useWsMutation } from "../../lib";
import { parseObjectSchema, SchemaFields } from "../../components/SchemaFields";
import { Tabs } from "../../components/Tabs";
import { ActionIde } from "./ActionIde";
import { AnsibleFields, rolesChosen, rolesPayload } from "./AnsibleFields";

/** Ce que le formulaire reprend de la version de départ (toutes les valeurs que l'API de versions accepte). */
export interface BaseVersion {
  version: string;
  runtime: string;
  source: string;
  entrypoint?: string;
  input_schema: unknown;
  output_schema: unknown;
  side_effects: string;
  idempotent: boolean;
  network_policy: string;
  license: string;
  limits?: Record<string, unknown>;
  examples?: unknown[];
  secrets_required?: unknown[];
  required_tags?: string[];
  ansible_roles?: { role: string; version: string }[];
}

const BUMPS: [string, string][] = [
  ["patch", "Correctif (1.0.0 → 1.0.1) : même contrat, code corrigé"],
  ["minor", "Mineure (1.0.0 → 1.1.0) : ajout compatible"],
  ["major", "Majeure (1.0.0 → 2.0.0) : le contrat change"],
];

/** Publie une nouvelle version d'une action existante, à partir de sa dernière version. Une version publiée reste immuable : on en crée une autre. */
export function NewVersionForm({ slug, base, onDone }: { slug: string; base: BaseVersion; onDone: () => void }) {
  const publish = useWsMutation<Record<string, unknown>, { version: string }>("POST", `/actions/${slug}/versions/`, [
    `versions-${slug}`,
    `action-${slug}`,
    "actions-lib",
    "actions",
    "ansible-roles",
  ]);
  const isAnsible = base.runtime === "ansible";
  const [f, setF] = useState({
    bump: "minor",
    changelog: "",
    source: base.source,
    input: JSON.stringify(base.input_schema, null, 2),
    output: JSON.stringify(base.output_schema, null, 2),
    side: base.side_effects,
    network: base.network_policy,
  });
  const [runnerTags, setRunnerTags] = useState((base.required_tags ?? []).join(", "));
  const [roles, setRoles] = useState<Record<string, string>>(rolesChosen(base.ansible_roles));
  const [jsonError, setJsonError] = useState("");
  const [tab, setTab] = useState("general");
  const [ide, setIde] = useState(false);
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  /** Les champs d'une version tels que l'API les attend, ou la raison pour laquelle le formulaire n'est pas utilisable (schémas non JSON). */
  const draft = (): { draft: Record<string, unknown> } | { error: string } => {
    let input_schema: unknown, output_schema: unknown;
    try {
      input_schema = JSON.parse(f.input);
      output_schema = JSON.parse(f.output);
    } catch {
      return { error: "Un des schémas n'est pas du JSON valide (onglets Entrées et Sorties)." };
    }
    return {
      draft: {
        runtime: base.runtime,
        source: f.source,
        input_schema,
        output_schema,
        side_effects: f.side,
        network_policy: f.network,
        idempotent: base.idempotent,
        license: base.license,
        entrypoint: base.entrypoint ?? "run",
        limits: base.limits ?? {},
        examples: base.examples ?? [],
        secrets_required: base.secrets_required ?? [],
        ...(isAnsible
          ? {
              required_tags: runnerTags
                .split(",")
                .map((t) => t.trim())
                .filter(Boolean),
              ansible_roles: rolesPayload(roles),
            }
          : {}),
      },
    };
  };
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const built = draft();
    if ("error" in built) {
      setJsonError(built.error);
      setTab(parseObjectSchema(f.input) ? "out" : "in");
      return;
    }
    setJsonError("");
    publish.mutate({ ...built.draft, bump: f.bump, changelog: f.changelog }, { onSuccess: onDone });
  };
  const problems = contractProblems(publish.error);
  if (ide)
    return (
      <ActionIde
        runtime={base.runtime}
        source={f.source}
        onSource={(v) => setF((cur) => ({ ...cur, source: v }))}
        slug={slug}
        inputSchema={f.input}
        buildDraft={draft}
        onBack={() => setIde(false)}
      />
    );
  const lines = f.source.split("\n").length;
  return (
    <form onSubmit={submit} aria-label="Nouvelle version" className="version-form">
      <div className="version-head">
        <span className="ver-pill">Version de départ {base.version}</span>
        <span className="ver-pill ver-pill-soft">{isAnsible ? "Ansible" : base.runtime}</span>
        <span className="hint">Elle reste inchangée ; la nouvelle version est publiée et immuable.</span>
      </div>
      <Tabs
        label="Sections de la nouvelle version"
        active={tab}
        onChange={setTab}
        tabs={[
          {
            id: "general",
            label: "Général",
            content: (
              <div className="version-grid">
                <section className="np-section" aria-labelledby="nv-s-version">
                  <h3 id="nv-s-version">Version</h3>
                  <div className="field">
                    <label htmlFor="nv-b">Type de changement</label>
                    <select id="nv-b" value={f.bump} onChange={set("bump")}>
                      {BUMPS.map(([v, l]) => (
                        <option key={v} value={v}>
                          {l}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="nv-l">Ce qui change (facultatif)</label>
                    <input id="nv-l" value={f.changelog} maxLength={300} onChange={set("changelog")} placeholder="ex. correctif du rôle web" />
                  </div>
                </section>
                <section className="np-section" aria-labelledby="nv-s-exec">
                  <h3 id="nv-s-exec">Exécution</h3>
                  <div className="field">
                    <label htmlFor="nv-e">Effets de bord</label>
                    <select id="nv-e" value={f.side} onChange={set("side")}>
                      <option value="none">Aucun effet externe</option>
                      <option value="external">Effet externe (non rejoué automatiquement si l'issue est incertaine)</option>
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="nv-n">Politique réseau</label>
                    <select id="nv-n" value={f.network} onChange={set("network")}>
                      <option value="none">Aucun accès réseau</option>
                      <option value="test-service">Service de test local uniquement</option>
                      {isAnsible && <option value="targets">Machines gérées (réseau de l'exploitant)</option>}
                    </select>
                  </div>
                </section>
                {isAnsible && (
                  <section className="np-section version-wide" aria-labelledby="nv-s-ans">
                    <h3 id="nv-s-ans">Ansible</h3>
                    <AnsibleFields runnerTags={runnerTags} setRunnerTags={setRunnerTags} roles={roles} setRoles={setRoles} idPrefix="nv" />
                  </section>
                )}
              </div>
            ),
          },
          {
            id: "in",
            label: "Entrées",
            content: (
              <>
                <SchemaFields kind="in" idPrefix="nv-in" value={f.input} onChange={(v) => setF((cur) => ({ ...cur, input: v }))} />
                <p className="hint">Pour chaque entrée : un type, un titre et une description. Le serveur refuse la publication d'un contrat incomplet.</p>
              </>
            ),
          },
          {
            id: "out",
            label: "Sorties",
            content: (
              <>
                <SchemaFields kind="out" idPrefix="nv-out" value={f.output} onChange={(v) => setF((cur) => ({ ...cur, output: v }))} />
                <p className="hint">Pour chaque sortie : un type, un titre et une description.</p>
              </>
            ),
          },
          {
            id: "code",
            label: isAnsible ? "Playbook" : "Code",
            content: (
              <div className="field">
                <p>
                  {isAnsible ? "Playbook YAML" : `Code ${base.runtime}`} : {lines} ligne(s)
                  {f.source !== base.source ? ` — modifié depuis la version ${base.version}` : ""}.
                </p>
                <p className="hint">
                  L'éditeur avancé offre la coloration, la complétion et l'essai du code dans le bac à sable, avec les paramètres de ce formulaire, avant toute
                  publication.
                </p>
                <button type="button" className="primary" onClick={() => setIde(true)}>
                  Ouvrir l'éditeur avancé
                </button>
              </div>
            ),
          },
        ]}
      />
      {jsonError && (
        <p role="alert" className="field-error">
          Erreur : {jsonError}
        </p>
      )}
      {problems ? (
        <div role="alert" className="field-error">
          Le contrat n'est pas complet :
          <ul>
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      ) : (
        publish.error && (
          <p role="alert" className="field-error">
            Erreur : {errorText(publish.error)}
          </p>
        )
      )}
      <div className="version-foot">
        <button type="submit" className="primary" disabled={publish.isPending}>
          Publier la nouvelle version
        </button>
      </div>
    </form>
  );
}

function contractProblems(e: unknown): string[] | null {
  const d = (e as Partial<ApiError> | null)?.details as { contract?: unknown } | null | undefined;
  return d && Array.isArray(d.contract) && d.contract.length ? (d.contract as string[]) : null;
}
